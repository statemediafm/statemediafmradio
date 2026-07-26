"""``maelcom serve``: the web server plus a background refresh loop.

The loop periodically polls the roster's sources and republishes two things to
the app state: the generative-music program (``/genmusic``) and the voiced news
plan (``/plan``). Music is recomputed every tick (cheap, deterministic); the
news plan is only re-voiced when the item set actually changes, so TTS isn't run
on every tick.

``refresh_once`` is a plain function (no web/async deps) so it is unit-testable
offline; ``run`` wires it into a uvicorn server via an async task.
"""

from __future__ import annotations

import sys
import time
from dataclasses import replace

from .core.models import Script
from .core.schedule import Programme, assemble_broadcast
from .genmusic import activity, compose
from .newsroom.summarize import Read, radio_reads, summarize
from .newsroom.tts import TTSProvider, render_reads

# Quiet-mode lead-in window before the news airs (seconds): 1–3 minutes.
_QUIET_LEAD_MIN, _QUIET_LEAD_MAX = 60, 180
_QUIET_TAIL = 60  # keep the music ~1 minute after the news, then go silent


def _segment_reads(items, style, headlines, llm) -> list[Read]:
    """The ``Read`` chunks for one segment.

    With a live ``llm`` (a ``(client, cfg)`` pair) the segment is written by the
    model — the LLM *parses* the activity into prose, mirroring ``voice --live``.
    A live-model failure degrades to the deterministic ``radio_reads`` copy and
    prints a note, so a flaky gateway can't take the broadcast off air. Offline
    (the default) it's the deterministic reads with their per-headline pauses and
    per-source voices.
    """
    if llm is not None:
        client, cfg = llm
        try:
            script = summarize(items, style, client=client, cfg=cfg)
            return [Read("other", script.text)]
        except Exception as exc:  # noqa: BLE001 — degrade to offline copy, stay on air
            print(f"live summarize failed ({exc}); using deterministic copy", file=sys.stderr)
    return radio_reads(items, style, max_headlines=headlines or 5)


def _publish_plan(state, per_topic, tts, style, headline_pause_ms, llm=None) -> None:
    voice = getattr(state, "voice", None)  # live-selectable narration voice
    programmes: list[Programme] = []
    content: dict = {}
    for topic, items, _cadence, headlines in per_topic:
        reads = _segment_reads(items, style, headlines, llm)
        script = Script(text=" ".join(r.text for r in reads), style=style)
        audio = render_reads(
            reads, tts, style=style, voice=voice, headline_pause_ms=headline_pause_ms
        )
        content[topic] = (script, audio)
        programmes.append(Programme(topic, _cadence))
    state.set_plan(assemble_broadcast(programmes, content, window_s=3600))


def _effective_llm(state, llm):
    """Apply the UI's live news-parsing overrides to the base LLM config.

    ``llm`` is the ``(client, base_cfg)`` wired at boot, or ``None`` when the
    server isn't running live. The Settings tab can override the gateway model
    (``state.news_model``) and the sampling knobs (``state.news_temperature``,
    ``state.news_max_tokens``) for this tick, so news parsing can be tuned live
    without a restart. Unset (``None``) overrides leave the base config's value.
    """
    if llm is None:
        return None
    client, base_cfg = llm
    overrides = {
        "model": getattr(state, "news_model", None),
        "temperature": getattr(state, "news_temperature", None),
        "max_tokens": getattr(state, "news_max_tokens", None),
    }
    overrides = {k: v for k, v in overrides.items() if v is not None}
    return (client, replace(base_cfg, **overrides)) if overrides else llm


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
    style: str = "bbc-world",
    headline_pause_ms: int = 1000,
    llm=None,
    director=None,
    now: float | None = None,
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
    news_due = director.news_due(cache["last_elapsed"], elapsed) if director is not None else True
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

    # Remember the latest activity so a live model/tuning switch can recompose.
    signal = activity(all_items)
    state.last_signal = signal
    # HOLD the journey once it is playing: a news/activity update must not
    # republish the program and restart the piece mid-stream (regenerated only
    # when there isn't one yet, or explicitly on a model/tuning switch).
    if state.program is None:
        state.set_program(
            compose(
                signal,
                style=getattr(state, "model", "Entrainment 0.1"),
                tuning_a=getattr(state, "tuning", 440.0),
            )
        )

    signature = tuple(sorted(item.id for item in all_items))
    changed = cache.get("news_sig") != signature
    # Apply the UI's live news-model selection to the wired LLM config.
    eff_llm = _effective_llm(state, llm)

    # Air a bulletin only when there's fresh activity AND a news slot is due.
    air_news = changed and news_due

    if not getattr(state, "quiet_mode", False):
        state.music_on = True
        if air_news:
            cache["news_sig"] = signature
            _publish_plan(state, per_topic, tts, style, headline_pause_ms, eff_llm)
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
        _publish_plan(state, cache["q_pending"], tts, style, headline_pause_ms, eff_llm)
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
    style: str = "bbc-world",
    generator: str = "Entrainment 0.1",
    show_selector: bool = False,
    generators_dir: str | None = None,
    llm=None,
    news_models: list[str] | None = None,
    segments: list[dict] | None = None,
    voice: str | None = None,
    news_every_s: float | None = None,
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
    the :class:`~maelcom.core.director.Director` decides when bulletins air; the
    music plays under song slots and station idents between them.
    """
    try:
        import asyncio

        import uvicorn

        from .genmusic.generators import load_generators, register_generators
        from .genmusic.styles import STYLES
        from .web.app import _State, create_app
    except ImportError as exc:
        raise SystemExit("serve needs the [web] extra: pip install -e '.[web]'") from exc

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
    # News-parsing model selection (Settings tab). Seed the current pick with the
    # wired model and offer the configured list (plus the current one) as options.
    if llm is not None:
        _client, base_cfg = llm
        state.news_model = base_cfg.model
        state.news_cfg = base_cfg  # lets the Settings tab auto-discover gateway models
        state.news_temperature = base_cfg.temperature
        state.news_max_tokens = base_cfg.max_tokens
        options = list(news_models or [])
        if base_cfg.model not in options:
            options.insert(0, base_cfg.model)
        state.news_models = options
    # The rhythm-of-the-day director: news bulletins on a 17-min cadence, song
    # slots + idents between, over a session clock the /schedule endpoint reads.
    from .core.director import Director
    from .core.schedule import Cadence

    director = Director(news=Cadence(news_every_s)) if news_every_s else Director()
    state.director = director
    start = time.monotonic()
    state.session_start = start
    app = create_app(state)
    cache: dict = {"t0": start, "last_elapsed": -1.0}

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
            await asyncio.sleep(refresh)

    print(
        f"maelcom serving on http://{host}:{port}  (refreshing every {refresh:.0f}s)",
        file=sys.stderr,
    )

    # Run the refresh loop as a task in the same event loop as the server.
    config = uvicorn.Config(app, host=host, port=port, log_level="warning")
    server = uvicorn.Server(config)

    async def _main() -> None:
        task = asyncio.create_task(_loop())
        try:
            await server.serve()
        finally:
            task.cancel()

    asyncio.run(_main())
    return 0
