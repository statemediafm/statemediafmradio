"""``statemediafm serve``: the web server plus a background refresh loop.

The loop periodically polls the roster's sources and republishes two things to
the app state: the generative-music program (``/genmusic``) and the voiced news
plan (``/plan``). Music is recomputed every tick (cheap, deterministic); the
news plan is only re-voiced when the item set actually changes, so TTS isn't run
on every tick.

``refresh_once`` is a plain function (no web/async deps) so it is unit-testable
offline; ``run`` wires it into a uvicorn server via an async task.
"""

from __future__ import annotations

import os
import sys
import time
from dataclasses import replace

from .core.models import Script
from .core.schedule import Programme, assemble_broadcast
from .genmusic import THETA_START, activity, compose
from .newsroom.summarize import Read, bulletin_reads, summarize
from .newsroom.tts import TTSProvider, render_reads

# Quiet-mode lead-in window before the news airs (seconds): 1–3 minutes.
_QUIET_LEAD_MIN, _QUIET_LEAD_MAX = 60, 180
_QUIET_TAIL = 60  # keep the music ~1 minute after the news, then go silent

# Demo Mode: reproduce the earlier-milestone feel — read Hacker News + a repo's
# git issues on a brisk 5-minute cadence, music in between. The toggle in
# Settings flips ``state.demo_mode`` (see the web app) and adds these sources.
DEMO_NEWS_EVERY_S = 120.0  # demo: a fresh reading every 2 minutes
DEMO_REPO = "https://github.com/meltano/meltano"
_DEMO_DIRECTOR = None


def _default_song_resolver(title: str, artist: str):
    """Resolve a song to a Spotify track from the gitignored credentials; None if
    Spotify isn't connected or the lookup fails (a flaky network can't break air)."""
    try:
        from .spotify import from_auth

        conn = from_auth()
        return conn.resolve(title, artist) if conn.configured else None
    except Exception:  # noqa: BLE001 — resolution is best-effort
        return None


def publish_song(state, song_resolver=None) -> None:
    """Fill the next song slot: pick the next playlist track, resolve it to a
    streamable Spotify track when possible, and publish it to ``state.song``."""
    from .songs import pick

    i = getattr(state, "song_i", 0)
    label, query = pick(i)
    state.song_i = i + 1
    # Resolve the generic mood/genre query via free-text search (no named artist).
    track = (song_resolver or _default_song_resolver)(query, "")
    state.song = {
        "title": getattr(track, "name", None) or label,
        "artist": getattr(track, "artist", None) or "",
        "url": getattr(track, "url", None),
        "uri": getattr(track, "uri", None),
        "preview_url": getattr(track, "preview_url", None),
        "source": "spotify" if track is not None else None,
    }


MIX_EVERY_S = 360.0  # rotate the ambient generator every ~6 minutes in mix mode


def _mix_generator(state, elapsed: float, cache: dict) -> str | None:
    """In mix mode, the generator for this tick: rotate the selected generators on
    the MIX cadence. Returns the generator only when it just CHANGED (so the caller
    recomposes then, not every tick); ``None`` when mix is off or unchanged."""
    if not getattr(state, "mix_generators", False):
        cache.pop("mix_gen", None)  # forget so re-enabling rotates cleanly
        return None
    from .genmusic.styles import AMBIENT_MODELS

    pool = list(getattr(state, "mix_models", None) or AMBIENT_MODELS)
    if not pool:
        return None
    gen = pool[int(elapsed // MIX_EVERY_S) % len(pool)]
    if gen == cache.get("mix_gen"):
        return None
    cache["mix_gen"] = gen
    return gen  # the rotated generator to compose with — state.model (the user's
    # own selection) is left untouched, so turning mixing off restores it


def _demo_director():
    """A cached 5-minute-cadence director used while Demo Mode is on."""
    global _DEMO_DIRECTOR
    if _DEMO_DIRECTOR is None:
        from .core.director import Director
        from .core.schedule import Cadence
        _DEMO_DIRECTOR = Director(news=Cadence(DEMO_NEWS_EVERY_S))
    return _DEMO_DIRECTOR


def _segment_reads(items, style, headlines, llm, *, ident=None, signoff=None) -> list[Read]:
    """The ``Read`` chunks for one segment — always LLM-written.

    ``llm`` is the ``(client, cfg)`` pair; the model *parses* the activity into
    prose, which is wrapped with the station ``ident``/``signoff`` and paced. There
    is no deterministic fallback: if no model is configured (``llm`` is ``None``) or
    the model call fails, the segment airs **nothing** this cycle (an empty list).
    """
    if llm is None:
        print("no news model configured; skipping this bulletin", file=sys.stderr)
        return []
    client, cfg = llm
    try:
        script = summarize(items, style, client=client, cfg=cfg)
        # Wrap with the station ident + sign-off; pace sentences/topics in between.
        return bulletin_reads(script.text, ident=ident, signoff=signoff)
    except Exception as exc:  # noqa: BLE001 — no fallback: skip the bulletin, stay on air
        print(f"live summarize failed ({exc}); skipping this bulletin", file=sys.stderr)
        return []


def _headlines(items, cap) -> list[tuple[str, str | None]]:
    """Deduped ``(title, url)`` pairs for the on-page linked list, capped like the
    spoken reads (``cap`` or 5). ``url`` is the item's first ref, or None."""
    out: list[tuple[str, str | None]] = []
    seen: set[str] = set()
    for it in items:
        title = (it.title or "").strip()
        if not title or title.lower() in seen:
            continue
        seen.add(title.lower())
        refs = getattr(it, "refs", None)
        out.append((title, refs[0] if refs else None))
        if len(out) >= (cap or 5):
            break
    return out


def _voice_rotation(base: str | None) -> list:
    """Voices to rotate across sources so each sounds distinct — the operator's
    ``base`` voice first (so the first source keeps it), then the other curated
    voices. A ``base`` that isn't a curated alias (a full Piper name/path) still
    leads, with the curated set behind it."""
    from .newsroom.tts import voice_names

    names = voice_names()
    if base in names:
        return [base] + [n for n in names if n != base]
    return [base, *names] if base else names


def _publish_plan(state, per_topic, tts, style, headline_pause_ms, llm=None, cache=None) -> None:
    base_voice = getattr(state, "voice", None)  # the operator's chosen narration voice
    ident = getattr(state, "ident", None)  # persona station phrasing
    signoff = getattr(state, "signoff", None)
    rotation = _voice_rotation(base_voice)
    # Give each SOURCE its own voice so git/forge updates sound distinct from Hacker
    # News, etc. The assignment is remembered in the tick cache by first appearance,
    # so a source keeps its voice across bulletins even when others fall silent.
    topic_voice = cache.setdefault("topic_voice", {}) if cache is not None else {}
    programmes: list[Programme] = []
    content: dict = {}
    for topic, items, _cadence, headlines in per_topic:
        reads = _segment_reads(items, style, headlines, llm, ident=ident, signoff=signoff)
        if not reads:
            continue  # no model / the model failed → this segment airs nothing
        script = Script(text=" ".join(r.text for r in reads if r.role != "pause"), style=style)
        if topic not in topic_voice and rotation:
            topic_voice[topic] = rotation[len(topic_voice) % len(rotation)]
        seg_voice = topic_voice.get(topic, base_voice)
        try:
            audio = render_reads(
                reads, tts, style=style, voice=seg_voice, headline_pause_ms=headline_pause_ms
            )
        except Exception:  # noqa: BLE001 — a per-source voice that can't load (e.g. an
            # offline model download) must not take the segment off air.
            audio = render_reads(
                reads, tts, style=style, voice=base_voice, headline_pause_ms=headline_pause_ms
            )
        content[topic] = (script, audio, _headlines(items, headlines))
        programmes.append(Programme(topic, _cadence))
    if not programmes:
        return  # nothing to air this cycle (no news model, or all segments failed)
    state.set_plan(assemble_broadcast(programmes, content, window_s=3600))


def _news_client(state, backend):
    """The cached news-writing client for ``backend``: the local Claude Code CLI
    (``claude -p``, using the operator's own auth) or the LiteLLM gateway."""
    if backend == "claude-cli":
        client = getattr(state, "_cli_client", None)
        if client is None:
            from .newsroom.llm import ClaudeCliClient

            client = ClaudeCliClient()
            state._cli_client = client
        return client
    client = getattr(state, "_llm_client", None)
    if client is None:
        from .newsroom.llm import LiteLLMClient

        client = LiteLLMClient()
        state._llm_client = client
    return client


def _effective_llm(state, llm):
    """The ``(client, cfg)`` for LLM news this tick, or ``None``. News is **always**
    LLM-written (no deterministic fallback); the *writer* is chosen by
    ``state.news_backend`` — the local **Claude Code CLI** (default; the operator's
    own auth, no key) or the **LLM gateway**. The client is the one wired at boot
    (legacy/tests), else lazily built + cached. For the gateway a model is required
    (``None`` → no bulletin); the CLI uses its own default model.
    """
    backend = getattr(state, "news_backend", None) or "gateway"
    if llm is not None:
        client, base_cfg = llm
    else:
        from .newsroom.llm import LLMConfig

        base_cfg = getattr(state, "news_cfg", None) or LLMConfig(
            model=getattr(state, "news_model", None) or ""
        )
        client = _news_client(state, backend)
    overrides = {
        "model": getattr(state, "news_model", None),
        "temperature": getattr(state, "news_temperature", None),
        "max_tokens": getattr(state, "news_max_tokens", None),
    }
    overrides = {k: v for k, v in overrides.items() if v is not None}
    cfg = replace(base_cfg, **overrides) if overrides else base_cfg
    # The gateway needs an explicit model; the Claude CLI falls back to its own.
    if llm is None and backend != "claude-cli" and not getattr(cfg, "model", None):
        return None
    return (client, cfg)


def _quiet_lead(signature: tuple) -> float:
    """A deterministic 1–3 minute lead-in derived from the news signature."""
    h = sum(ord(c) for c in "".join(signature))
    return _QUIET_LEAD_MIN + h % (_QUIET_LEAD_MAX - _QUIET_LEAD_MIN + 1)


def refresh_once(
    state,
    roster: list,
    tts: TTSProvider,
    *,
    cache: dict,
    style: str = "newsroom",
    headline_pause_ms: int = 1000,
    llm=None,
    director=None,
    now: float | None = None,
    song_resolver=None,
) -> None:
    """One refresh: recompute the music program, and the news plan if changed.

    ``roster`` is ``(topic, source, cadence, headlines)`` entries. Sources that
    error or return nothing are skipped. ``cache`` (a dict owned by the caller)
    holds the last item-set signature so the news plan is only re-voiced when the
    activity actually changed.

    ``director`` (the rhythm-of-the-day clock) gates *when* a bulletin airs: news
    is published only when a news slot has fallen due since the last tick (the
    17-minute cadence) **and** there is fresh activity — hybrid cadence+recency.
    Without a director, news airs on every change (the older behaviour).

    In **quiet mode** the news is held back so the music can lead in 1–3 minutes
    *before* it airs, stays ~1 minute after, then goes silent (``state.music_on``)
    until the next news cycle. ``now`` (a monotonic clock) is injectable for tests.
    """
    now = time.monotonic() if now is None else now
    # Broadcast off: do no work — no polling, no TTS, no LLM (stop consuming).
    if not getattr(state, "broadcasting", True):
        return
    # Session-relative clock for the rhythm: is a news bulletin due this tick?
    if "t0" not in cache:
        cache["t0"] = now
        cache["last_elapsed"] = -1.0  # so the opening bulletin airs on the first tick
    elapsed = now - cache["t0"]
    # Demo Mode overrides the rhythm with a brisk 5-minute news cadence.
    active_director = _demo_director() if getattr(state, "demo_mode", False) else director
    news_due = (
        active_director.news_due(cache["last_elapsed"], elapsed)
        if active_director is not None
        else True
    )
    # Song slots: a familiar song drops between bulletins when the user opts to mix
    # Spotify in (and Spotify is connected). Gated by the director's song cadence.
    if (
        active_director is not None
        and getattr(state, "mix_spotify", False)
        and active_director.song_due(cache["last_elapsed"], elapsed)
    ):
        publish_song(state, song_resolver)
    cache["last_elapsed"] = elapsed
    style = getattr(state, "style", None) or style  # live-selectable writing style
    per_topic: list[tuple] = []
    all_items: list = []
    # Snapshot: the Settings tab can add/remove sources on another thread mid-tick.
    for topic, source, cadence, headlines in list(roster):
        try:
            items = source.poll()
        except OSError:  # network / API failure — skip this source this tick
            continue
        if not items:
            continue
        per_topic.append((topic, items, cadence, headlines))
        all_items.extend(items)

    if not all_items:
        return
    cache["last_per_topic"] = per_topic  # so "Newscast now" can re-air without re-polling

    # Remember the latest activity so a live model/tuning switch can recompose.
    signal = activity(all_items)
    state.last_signal = signal
    # HOLD the journey once it is playing: a news/activity update must not
    # republish the program and restart the piece mid-stream (regenerated only
    # when there isn't one yet, or explicitly on a model/tuning switch).
    #
    # MIX MODE: when the user opts to mix ambient generators, rotate through the
    # selected generators on a slow cadence (a DJ-style change of bed), recomposing
    # at each turn. Otherwise a single generator holds.
    gen = _mix_generator(state, elapsed, cache)
    if gen is not None or state.program is None:
        state.set_program(
            compose(
                signal,
                style=gen or getattr(state, "model", "Entrainment 0.1"),
                tuning_a=getattr(state, "tuning", 440.0),
                base_intensity=getattr(state, "base_intensity", THETA_START),
            )
        )

    signature = tuple(sorted(item.id for item in all_items))
    changed = cache.get("news_sig") != signature
    # Apply the UI's live news-model selection to the wired LLM config.
    eff_llm = _effective_llm(state, llm)

    # Air a bulletin when a news slot is due AND there's fresh activity — except in
    # Demo Mode, which re-reads the current sources every slot (so the rhythm is
    # visible even when the front page hasn't changed).
    air_news = news_due if getattr(state, "demo_mode", False) else (changed and news_due)

    if not getattr(state, "quiet_mode", False):
        state.music_on = True
        if air_news:
            cache["news_sig"] = signature
            _publish_plan(state, per_topic, tts, style, headline_pause_ms, eff_llm, cache)
        return

    # ── Quiet mode: gate the music around the news broadcast ─────────────────
    if air_news and not cache.get("q_pending"):
        # New news → start a cycle: the music leads in now; hold the news to air
        # after the lead-in.
        cache["news_sig"] = signature
        cache["q_pending"] = per_topic
        state.music_on = True
        cache["q_air_at"] = now + _quiet_lead(signature)
        cache["q_off_at"] = None
    if cache.get("q_pending") and now >= cache.get("q_air_at", now):
        _publish_plan(state, cache["q_pending"], tts, style, headline_pause_ms, eff_llm, cache)
        cache["q_pending"] = None
        cache["q_off_at"] = now + _QUIET_TAIL  # then a 1-minute tail
    if cache.get("q_off_at") is not None and now >= cache["q_off_at"]:
        state.music_on = False  # silence until the next news cycle
        cache["q_off_at"] = None


def run(
    roster: list,
    tts: TTSProvider,
    *,
    host: str = "127.0.0.1",
    port: int = 8000,
    refresh: float = 60.0,
    headline_pause_ms: int = 1000,
    style: str = "newsroom",
    generator: str = "Entrainment 0.1",
    show_selector: bool = False,
    generators_dir: str | None = None,
    llm=None,
    news_models: list[str] | None = None,
    segments: list[dict] | None = None,
    voice: str | None = None,
    news_every_s: float | None = None,
    base_intensity: float | None = None,
    quiet_mode: bool = False,
    mix: dict | None = None,
    persist: bool = False,
    live: bool = False,
    news_backend: str | None = None,
    news_cfg=None,
    news_model: str | None = None,
    news_temperature: float | None = None,
    news_max_tokens: int | None = None,
    open_browser: bool = False,
) -> int:
    """Boot the FastAPI app and drive ``refresh_once`` on an interval.

    The **ambient generator** is a config item: ``generator`` is the default
    model; ``show_selector`` controls whether the UI dropdown is shown (off by
    default); ``generators_dir`` is an optional directory of user/contributor
    generator configs to register before serving.

    ``llm`` is the ``(client, cfg)`` pair for LLM-written news (``--live``), or
    ``None`` for the deterministic offline copy. ``news_models`` lists the
    gateway models the Settings tab offers for news parsing; the current pick
    lives in ``state.news_model`` and can be changed live.

    ``news_every_s`` sets the **rhythm-of-the-day** news cadence (default 17 min):
    the :class:`~statemediafm.core.director.Director` decides when bulletins air; the
    music plays under song slots and station idents between them.
    """
    try:
        import asyncio

        import uvicorn

        from .genmusic.generators import load_generators, register_generators
        from .genmusic.styles import STYLES
        from .web.app import _State, create_app, new_security_policy
    except ImportError as exc:
        raise SystemExit("serve needs the [web] extra: pip install -e '.[web]'") from exc

    # Control-API security: refuse to expose an unauthenticated API off loopback.
    # Auth + a Host/Origin lock are always enforced; a non-loopback bind (where the
    # port is network-reachable) additionally requires an explicit opt-in so it
    # can't happen by accident. See SECURITY_MODEL.md.
    from .web.app import _host_only

    if _host_only(host) not in {"127.0.0.1", "::1", "localhost"}:
        if os.environ.get("STATEMEDIAFM_ALLOW_NONLOOPBACK") != "1":
            raise SystemExit(
                f"refusing to bind non-loopback host {host!r}: the control API would be "
                "network-reachable. Set STATEMEDIAFM_ALLOW_NONLOOPBACK=1 to proceed "
                "(session-token auth + a Host/Origin lock stay enforced)."
            )
        print(
            f"WARNING: binding {host!r} — the control API is network-reachable. It "
            "stays token-protected and host-locked; open the UI from the served page "
            "to obtain the session token.",
            file=sys.stderr,
        )

    if generators_dir:  # register user/contributor generators before serving
        register_generators(load_generators(generators_dir))

    state = _State()
    if generator in STYLES:
        state.model = generator
    else:
        print(f"unknown generator {generator!r}; using {state.model!r}", file=sys.stderr)
    state.show_selector = show_selector
    # Narration: the writing style and voice are live-selectable from Settings.
    state.style = style
    if voice:
        state.voice = voice
    # The live roster is owned by the app state so the Settings tab can edit it.
    state.roster = list(roster)
    state.segments = list(segments or [])
    # Restore persisted (non-flag) settings: energy, quiet mode, mix toggles.
    if base_intensity is not None:
        state.base_intensity = float(base_intensity)
    state.quiet_mode = bool(quiet_mode)
    if mix:
        state.mix_generators = bool(mix.get("generators", state.mix_generators))
        state.mix_models = list(mix.get("models", state.mix_models))
        state.mix_spotify = bool(mix.get("spotify", state.mix_spotify))
    # News-parsing (Settings tab). `live` may be set by --live OR persisted; it can
    # also be toggled at runtime from the UI (see /news-live). A base `news_cfg` is
    # always seeded (even when off) so the model picker + gateway Discover work; the
    # gateway URL/key resolve from the auth file at call time. A wired boot `llm`
    # (legacy --live) still supplies the base config.
    if llm is not None and news_cfg is None:
        _client, news_cfg = llm
    state.live = bool(live)
    if news_backend:
        state.news_backend = news_backend
    if news_cfg is not None:
        state.news_cfg = news_cfg
        state.news_model = news_model or news_cfg.model
        state.news_temperature = (
            news_temperature if news_temperature is not None else news_cfg.temperature
        )
        state.news_max_tokens = (
            news_max_tokens if news_max_tokens is not None else news_cfg.max_tokens
        )
    elif news_model:
        state.news_model = news_model
    options = list(news_models or [])
    if state.news_model and state.news_model not in options:
        options.insert(0, state.news_model)
    state.news_models = options
    # The rhythm-of-the-day director: news bulletins on a 17-min cadence, song
    # slots + idents between, over a session clock the /schedule endpoint reads.
    from .core.director import Director
    from .core.schedule import Cadence

    director = Director(news=Cadence(news_every_s)) if news_every_s else Director()
    state.director = director
    # Cadences are live-editable from Settings: the loop reads state.refresh_s each
    # tick, and /cadence re-times director.news.
    state.news_every_s = director.news.every_s
    state.refresh_s = float(refresh)
    start = time.monotonic()
    # Persist UI changes to the settings file so they survive a restart (the
    # persist middleware in create_app calls this after each successful mutation).
    if persist:
        from .configstore import config_path, save_config, state_to_config

        state.on_change = lambda: save_config(state_to_config(state))
        if not config_path().exists():
            state.on_change()  # first run: capture the default roster (Hacker News) + settings
    # Extra Host/Origin names the control API answers to — needed to reach a
    # non-loopback bind from another machine (browse via the server's hostname/IP).
    # Comma-separated in $STATEMEDIAFM_ALLOWED_HOSTS.
    extra_hosts = [h for h in os.environ.get("STATEMEDIAFM_ALLOWED_HOSTS", "").split(",") if h.strip()]
    security = new_security_policy(host=host, extra_hosts=extra_hosts)
    app = create_app(state, security=security)
    cache: dict = {"t0": start, "last_elapsed": -1.0}

    def _air_news_now() -> bool:
        """Re-air a bulletin from the most recent activity (set by refresh_once),
        without polling sources or touching the poll/news timers."""
        per_topic = cache.get("last_per_topic")
        if not per_topic:
            return False
        eff_style = getattr(state, "style", None) or style
        _publish_plan(
            state, per_topic, tts, eff_style, headline_pause_ms, _effective_llm(state, llm), cache
        )
        return True

    state.air_news_now = _air_news_now

    async def _loop() -> None:
        while True:
            try:
                await asyncio.to_thread(
                    refresh_once,
                    state,
                    state.roster,  # live roster — the Settings tab can edit it
                    tts,
                    cache=cache,
                    style=style,
                    headline_pause_ms=headline_pause_ms,
                    llm=llm,
                    director=director,
                )
            except Exception as exc:  # noqa: BLE001 — one bad tick must not kill the loop
                print(f"refresh error: {exc}", file=sys.stderr)
            await asyncio.sleep(getattr(state, "refresh_s", refresh))  # live-editable

    print(
        f"statemediafm serving on http://{host}:{port}  (refreshing every {refresh:.0f}s)",
        file=sys.stderr,
    )

    # Run the refresh loop as a task in the same event loop as the server.
    config = uvicorn.Config(app, host=host, port=port, log_level="warning")
    server = uvicorn.Server(config)

    async def _open_when_up() -> None:
        import webbrowser

        from .web.app import _host_only

        await asyncio.sleep(1.2)  # let uvicorn bind before opening the tab
        shown = "127.0.0.1" if _host_only(host) in ("0.0.0.0", "") else host
        await asyncio.to_thread(webbrowser.open, f"http://{shown}:{port}")

    async def _main() -> None:
        task = asyncio.create_task(_loop())
        if open_browser:
            asyncio.create_task(_open_when_up())
        try:
            await server.serve()
        finally:
            task.cancel()

    asyncio.run(_main())
    return 0
