"""FastAPI app for the M1 slice: /health, /plan, /audio/{id}, and a minimal page.

The web layer is deliberately thin (plan §5.6): it holds the latest plan + an
in-memory audio store and renders/serves them. It contains no summarization or
scheduling logic. FastAPI is imported lazily so the rest of the package (and the
CLI/tests) work without the web dependency installed.
"""

from __future__ import annotations

from ..core.models import AudioRef, BroadcastPlan, StrudelProgram
from ..core.plan import plan_to_dict


def _recompose(store) -> None:
    """Regenerate the music program from the last activity with the current model,
    tuning and base energy — the shared body of the /model, /tuning and /intensity
    switches. No-op until there's a signal to compose from."""
    if store.last_signal is None:
        return
    from ..genmusic import compose

    store.set_program(
        compose(
            store.last_signal,
            style=store.model,
            tuning_a=store.tuning,
            base_intensity=store.base_intensity,
        )
    )


def program_to_dict(program: StrudelProgram) -> dict:
    """JSON view of a StrudelProgram — the client plays ``text`` and crossfades
    over ``fade_ms`` between polls."""
    return {
        "text": program.text,
        "style": program.style,
        "intensity": program.intensity,
        "brainwave_band": program.brainwave_band,
        "fade_ms": program.fade_ms,
    }


class _State:
    """In-memory store for the current plan, audio clips, and music (M1–M2)."""

    def __init__(self) -> None:
        self.plan: BroadcastPlan | None = None
        self.audio: dict[str, AudioRef] = {}
        self.program: StrudelProgram | None = None
        self.model: str = "Entrainment 0.1"  # the selected ambient generator (default)
        self.show_selector: bool = True  # show the ambient-generator dropdown? (config, on by default)
        self.mix_generators: bool = False  # rotate through several ambient generators over time
        self.mix_models: list[str] = []  # which generators are in the mix (empty → all)
        self.mix_spotify: bool = False  # mix Spotify songs into the song slots (needs Spotify + M5)
        self.tuning: float = 440.0  # concert-A reference (Hz) for all notes
        self.base_intensity: float = 0.25  # user base energy 0..1 (THETA_START); news lifts it
        self.broadcasting: bool = True  # when False the refresh loop pauses (no polling/TTS/LLM)
        self.quiet_mode: bool = False  # music only around the news, silent between
        self.music_on: bool = True  # the quiet-mode gate (should the music sound now?)
        self.demo_mode: bool = False  # earlier-milestone feel: HN+git issues every 5 min
        self.demo_topics: list[str] = []  # source topics Demo Mode added (to remove on off)
        self.last_signal = None  # last ActivitySignal, for immediate model/tuning switches
        self.news_model: str | None = None  # LLM model for news parsing (None → offline copy)
        self.news_models: list[str] = []  # gateway models the Settings tab offers
        self.news_cfg = None  # base LLMConfig (for gateway model auto-discovery)
        self.news_temperature: float | None = None  # live [llm] sampling override
        self.news_max_tokens: int | None = None  # live [llm] length override
        self.style: str = "bbc-world"  # live-selectable writing style for the news
        self.voice: str = "alan"  # live-selectable narration voice (Piper)
        self.persona: str | None = None  # selected themed persona (None → Custom)
        self.ident: str | None = None  # persona station-ident line (None → default)
        self.signoff: str | None = None  # persona sign-off line (None → default)
        # Live roster: the refresh loop reads these; the Settings tab edits them.
        self.roster: list = []  # (topic, source, cadence, headlines) entries
        self.segments: list[dict] = []  # the segment dicts behind roster (for display)
        self.director = None  # rhythm-of-the-day clock (Director), set by serve.run
        self.session_start: float | None = None  # monotonic session start, for /schedule

    def set_plan(self, plan: BroadcastPlan) -> None:
        self.plan = plan
        self.audio = {s.audio.id: s.audio for s in plan.segments if s.audio}

    def set_program(self, program: StrudelProgram) -> None:
        self.program = program


def create_app(state: _State | None = None):
    """Build the FastAPI application. Call ``app.state.store.set_plan(...)`` to
    publish a plan for the page and API to serve."""
    from fastapi import Body, FastAPI, HTTPException, Response
    from fastapi.responses import HTMLResponse

    store = state or _State()
    app = FastAPI(title="State Media FM", version="0.1.0")
    app.state.store = store

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    @app.get("/plan")
    def plan() -> dict:
        if store.plan is None:
            return {"segments": []}
        return plan_to_dict(store.plan)

    @app.get("/schedule")
    def schedule() -> dict:
        """The rhythm-of-the-day running order: the hour's news bulletins, song
        slots and station idents (relative offsets), plus where 'now' sits and
        the next foreground cue. ``live`` is False until ``serve`` sets a director."""
        director = store.director
        if director is None:
            return {"live": False, "order": [], "elapsed_s": 0.0}
        import time

        elapsed = (time.monotonic() - store.session_start) if store.session_start else 0.0
        order = [
            {"kind": c.kind, "at_s": c.at_s, "topic": c.topic,
             "song": (c.cue.title if c.cue else None)}
            for c in director.running_order(3600.0)
        ]
        nxt = director.next_cue(elapsed)
        return {
            "live": True,
            "elapsed_s": elapsed,
            "window_s": 3600.0,
            "order": order,
            "next": ({"kind": nxt.kind, "at_s": nxt.at_s} if nxt else None),
        }

    @app.get("/genmusic")
    def genmusic() -> dict:
        if store.program is None:
            return {"text": None, "play": store.music_on}
        # `play` is the quiet-mode gate: the client silences the music when False.
        return {**program_to_dict(store.program), "play": store.music_on}

    @app.get("/broadcast")
    def broadcast() -> dict:
        return {"broadcasting": store.broadcasting}

    @app.post("/broadcast")
    def set_broadcast(on: bool) -> dict:
        """Stop/resume the broadcast. Stopping pauses the server refresh loop
        (no more polling/TTS/LLM) and silences the music; resuming restores both."""
        store.broadcasting = on
        store.music_on = on  # silence the audio when stopped; restore on resume
        return {"broadcasting": store.broadcasting}

    @app.get("/quiet")
    def quiet() -> dict:
        return {"quiet_mode": store.quiet_mode, "music_on": store.music_on}

    @app.post("/quiet")
    def set_quiet(on: bool) -> dict:
        """Turn quiet mode on/off. Off resumes continuous play immediately."""
        store.quiet_mode = on
        if not on:
            store.music_on = True
        return {"quiet_mode": store.quiet_mode, "music_on": store.music_on}

    @app.get("/demo")
    def demo() -> dict:
        return {"demo_mode": store.demo_mode}

    @app.post("/demo")
    def set_demo(on: bool) -> dict:
        """Demo Mode: the earlier-milestone feel. Turning it on adds Hacker News
        and a repo's git-issues sources (if not already present) and switches the
        news to a brisk 5-minute cadence (handled in the refresh loop); music
        plays continuously in between. Turning it off removes the sources it added
        and restores the normal rhythm."""
        from ..roster import build_segment
        from ..serve import DEMO_REPO

        if on and not store.demo_mode:
            store.demo_mode = True
            store.quiet_mode = False  # music continuous between readings
            store.music_on = True
            wanted = [
                {"topic": "Hacker News front page", "source": "hackernews"},
                {"topic": "Engineering issues", "source": "repo", "repo": DEMO_REPO},
            ]
            existing = {s.get("topic") for s in store.segments}
            for seg in wanted:
                if seg["topic"] in existing:
                    continue
                try:
                    entry = build_segment(seg, len(store.segments))
                except (ValueError, KeyError):
                    continue
                store.segments.append(dict(seg))
                store.roster.append(entry)
                store.demo_topics.append(seg["topic"])
        elif not on and store.demo_mode:
            store.demo_mode = False
            # Remove only the sources Demo Mode added, leaving user-added ones.
            for topic in list(store.demo_topics):
                for i, s in enumerate(store.segments):
                    if s.get("topic") == topic:
                        store.segments.pop(i)
                        if i < len(store.roster):
                            store.roster.pop(i)
                        break
            store.demo_topics = []
        return {"demo_mode": store.demo_mode}

    @app.get("/models")
    def models() -> dict:
        """The user-selectable ambient generators, the current one, and whether
        the UI should show the selector at all (off by default, set by config)."""
        from ..genmusic.styles import AMBIENT_MODELS

        return {
            "models": list(AMBIENT_MODELS),
            "current": store.model,
            "selector": store.show_selector,
        }

    @app.post("/model")
    def set_model(name: str) -> dict:
        """Switch the ambient generator; recompose immediately if we have a signal."""
        from ..genmusic.styles import STYLES

        if name not in STYLES:
            raise HTTPException(status_code=400, detail="unknown model")
        store.model = name
        _recompose(store)
        return {"current": store.model}

    def _mix_status() -> dict:
        from ..auth import source_endpoint, source_token
        from ..genmusic.styles import AMBIENT_MODELS

        return {
            "mix_generators": store.mix_generators,
            "models": list(AMBIENT_MODELS),
            "selected": store.mix_models or list(AMBIENT_MODELS),
            "mix_spotify": store.mix_spotify,
            "spotify_configured": bool(source_endpoint("spotify") and source_token("spotify")),
        }

    @app.get("/mix")
    def mix() -> dict:
        """Mix settings: whether to rotate ambient generators, which ones, and
        whether to mix Spotify songs into the song slots."""
        return _mix_status()

    @app.post("/mix")
    def set_mix(payload: dict = Body(...)) -> dict:  # noqa: B008 (FastAPI body param)
        """Update the mix settings."""
        from ..genmusic.styles import AMBIENT_MODELS

        if "mix_generators" in payload:
            store.mix_generators = bool(payload["mix_generators"])
        if "mix_spotify" in payload:
            store.mix_spotify = bool(payload["mix_spotify"])
        if isinstance(payload.get("selected"), list):
            valid = [m for m in payload["selected"] if m in AMBIENT_MODELS]
            store.mix_models = valid
        return _mix_status()

    @app.get("/tuning")
    def tuning() -> dict:
        """The selectable concert-A tuning references (Hz) and the current one."""
        from ..genmusic.compose import TUNINGS

        return {"tunings": list(TUNINGS), "current": store.tuning}

    @app.post("/tuning")
    def set_tuning(a: float) -> dict:
        """Set the concert-A reference (Hz); recompose immediately if we have a signal."""
        from ..genmusic.compose import TUNINGS

        if a not in TUNINGS:
            raise HTTPException(status_code=400, detail="unsupported tuning")
        store.tuning = a
        _recompose(store)
        return {"current": store.tuning}

    @app.get("/intensity")
    def intensity() -> dict:
        """The user's base energy (0..1). Sessions start at this brainwave level;
        news activity lifts the music above it (plan §5.3)."""
        from ..genmusic import band_for_intensity

        return {"current": store.base_intensity, "band": band_for_intensity(store.base_intensity)}

    @app.post("/intensity")
    def set_intensity(level: float) -> dict:
        """Set the base energy 0..1; recompose immediately if we have a signal."""
        from ..genmusic import band_for_intensity

        if not 0.0 <= level <= 1.0:
            raise HTTPException(status_code=400, detail="intensity must be 0..1")
        store.base_intensity = level
        _recompose(store)
        return {"current": store.base_intensity, "band": band_for_intensity(level)}

    # A few writing-style suggestions for the UI; the field accepts any string.
    _STYLE_SUGGESTIONS = ["bbc-world", "npr", "sports-desk", "tech-brief", "noir"]

    @app.get("/style")
    def style() -> dict:
        """The current news writing style and a few suggestions (free-form)."""
        return {"current": store.style, "suggestions": _STYLE_SUGGESTIONS}

    @app.post("/style")
    def set_style(name: str) -> dict:
        """Set the news writing style (a free-form prompt hint). Next cycle."""
        name = name.strip()
        if not name:
            raise HTTPException(status_code=400, detail="empty style")
        store.style = name
        return {"current": store.style}

    @app.get("/voice")
    def voice() -> dict:
        """The current narration voice and the offered Piper voices."""
        from ..newsroom.tts import voice_names

        return {"current": store.voice, "voices": voice_names()}

    @app.post("/voice")
    def set_voice(name: str) -> dict:
        """Set the narration voice (a curated Piper voice). Next cycle."""
        from ..newsroom.tts import voice_names

        if name not in voice_names():
            raise HTTPException(status_code=400, detail="unknown voice")
        store.voice = name
        return {"current": store.voice}

    @app.get("/persona")
    def persona() -> dict:
        """The themed personas (a **commercial module**), the current pick, and
        whether the ``voice-personas`` module is licensed. Unlicensed, only
        ``Custom`` (free-form style/voice, default phrasing) may be selected."""
        from ..licensing import entitled
        from ..newsroom.personas import MODULE, persona_names

        return {
            "current": store.persona or "Custom",
            "personas": persona_names(),
            "licensed": entitled(MODULE),
            "module": MODULE,
        }

    @app.post("/persona")
    def set_persona(name: str) -> dict:
        """Select a persona — sets style, voice and station phrasing together — or
        ``Custom`` to keep the free-form controls and default phrasing. Selecting a
        persona requires the ``voice-personas`` license (402 otherwise). Next cycle."""
        from ..licensing import LicenseError, require
        from ..newsroom.personas import MODULE, get_persona

        if name == "Custom":
            store.persona = store.ident = store.signoff = None
            return {"current": "Custom", "style": store.style, "voice": store.voice}
        p = get_persona(name)
        if p is None:
            raise HTTPException(status_code=400, detail="unknown persona")
        try:
            require(MODULE)  # commercial gate — open-core has Custom only
        except LicenseError as exc:
            raise HTTPException(status_code=402, detail=str(exc)) from exc
        store.persona = p.name
        store.style, store.voice = p.style, p.voice
        store.ident, store.signoff = p.ident, p.signoff
        return {"current": p.name, "style": p.style, "voice": p.voice}

    @app.get("/license")
    def license_() -> dict:
        """Licensing status: whether a key is present and which commercial modules
        it unlocks (the key itself is never returned)."""
        from ..licensing import license_status

        return license_status()

    @app.post("/license")
    def set_license(payload: dict = Body(...)) -> dict:  # noqa: B008 (FastAPI body param)
        """Save a license key (taken from the request body, never the URL) to the
        gitignored license file, unlocking its commercial modules."""
        from ..licensing import license_status, save_license

        key = (payload.get("key") or "").strip()
        if not key:
            raise HTTPException(status_code=400, detail="empty key")
        save_license(key)
        return license_status()

    @app.get("/news-model")
    def news_model() -> dict:
        """The gateway model used for news parsing: the current pick, the offered
        options, and whether news parsing is live at all (``live`` is False when
        the server was started without ``--live`` — the news is the deterministic
        offline copy and the selector is hidden)."""
        return {
            "current": store.news_model,
            "models": list(store.news_models),
            "live": store.news_model is not None,
            "temperature": store.news_temperature,
            "max_tokens": store.news_max_tokens,
        }

    @app.post("/news-model")
    def set_news_model(
        name: str, temperature: float | None = None, max_tokens: int | None = None
    ) -> dict:
        """Switch the news-parsing model and (optionally) its sampling knobs — any
        model the gateway serves, plus ``temperature`` / ``max_tokens``. Applies to
        the next news cycle; only meaningful when the server is running live."""
        if store.news_model is None:
            raise HTTPException(status_code=409, detail="news parsing is not live")
        if temperature is not None and not 0.0 <= temperature <= 2.0:
            raise HTTPException(status_code=400, detail="temperature must be 0..2")
        if max_tokens is not None and max_tokens <= 0:
            raise HTTPException(status_code=400, detail="max_tokens must be positive")
        store.news_temperature = temperature
        store.news_max_tokens = max_tokens
        name = name.strip()
        if not name:
            raise HTTPException(status_code=400, detail="empty model")
        store.news_model = name
        if name not in store.news_models:
            store.news_models.append(name)  # remember a custom entry
        return {
            "current": store.news_model,
            "models": list(store.news_models),
            "temperature": store.news_temperature,
            "max_tokens": store.news_max_tokens,
        }

    @app.post("/news-model/discover")
    def discover_news_models() -> dict:
        """Auto-discover models the gateway serves (OpenAI-compatible
        ``GET {base}/models``) and merge them into the selectable options.
        Best-effort: an unreachable gateway just adds nothing."""
        if store.news_model is None:
            raise HTTPException(status_code=409, detail="news parsing is not live")
        from ..newsroom.llm import LLMConfig, discover_models

        cfg = store.news_cfg or LLMConfig(model=store.news_model)
        found = discover_models(cfg)
        merged = list(store.news_models)
        merged.extend(m for m in found if m not in merged)
        store.news_models = merged
        return {"models": merged, "discovered": found}

    @app.get("/sources")
    def sources() -> dict:
        """The live roster (which sources air) and the registered source kinds.
        Tokens are never included — those live in the auth tab."""
        from ..roster import source_kinds

        items = [
            {
                "index": i,
                "topic": entry[0],
                "kind": seg.get("source"),
                "every": seg.get("every", "15m"),
                "config": {k: v for k, v in seg.items() if k != "token"},
            }
            for i, (seg, entry) in enumerate(zip(store.segments, store.roster))
        ]
        return {"sources": items, "kinds": source_kinds()}

    @app.post("/sources")
    def add_source(seg: dict = Body(...)) -> dict:  # noqa: B008 (FastAPI body param)
        """Add a source to the live roster (this session only — not written to the
        config file). ``seg`` is a segment dict: a ``source`` kind plus its params
        (e.g. ``channel`` for slack, ``project`` for jira, ``repo`` for repo)."""
        from ..roster import build_segment

        if not isinstance(seg, dict) or not seg.get("source"):
            raise HTTPException(status_code=400, detail="a 'source' kind is required")
        try:
            entry = build_segment(seg, len(store.segments))
        except (ValueError, KeyError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        store.segments.append(dict(seg))
        store.roster.append(entry)
        return {"index": len(store.roster) - 1, "topic": entry[0]}

    @app.delete("/sources/{index}")
    def remove_source(index: int) -> dict:
        """Remove a source from the live roster by index."""
        if not 0 <= index < len(store.roster):
            raise HTTPException(status_code=404, detail="no such source")
        store.roster.pop(index)
        seg = store.segments.pop(index) if index < len(store.segments) else {}
        return {"removed": index, "topic": seg.get("topic")}

    @app.get("/llm-presets")
    def llm_presets() -> dict:
        """Quick-fill presets for the ``llm-gateway`` row (Azure, OpenRouter,
        vLLM, Ollama, NIM, …) — endpoint + example model, no credentials."""
        from ..newsroom.llm import GATEWAY_PRESETS

        return {"presets": GATEWAY_PRESETS}

    @app.get("/auth")
    def auth() -> dict:
        """Endpoints + whether a token is set (masked), split into activity news
        ``sources`` and model/LLM ``gateways`` (configured separately in the UI)."""
        from ..auth import AUTH_GATEWAYS, AUTH_NEWS_SOURCES, masked_auth

        return {
            "sources": list(AUTH_NEWS_SOURCES),
            "gateways": list(AUTH_GATEWAYS),
            "config": masked_auth(),
        }

    @app.post("/auth")
    def set_auth(payload: dict = Body(...)) -> dict:  # noqa: B008 (FastAPI body param)
        """Save a source's endpoint/token to the gitignored local auth file. The
        token is taken from the request body (never the URL) and only overwritten
        when a non-empty value is supplied."""
        from ..auth import AUTH_SOURCES, masked_auth, save_auth_entry

        source = payload.get("source")
        if source not in AUTH_SOURCES:
            raise HTTPException(status_code=400, detail="unknown source")
        save_auth_entry(
            source,
            endpoint=(payload.get("endpoint") or None),
            token=(payload.get("token") or None),
        )
        return {"config": masked_auth()}

    def _spotify_status() -> dict:
        from ..auth import source_endpoint, source_token

        cid = source_endpoint("spotify") or ""
        return {
            "client_id": cid,  # the Client ID is not a secret; the secret is masked
            "secret_set": bool(source_token("spotify")),
            "configured": bool(cid and source_token("spotify")),
        }

    @app.get("/spotify")
    def spotify() -> dict:
        """Spotify connection status: the Client ID and whether a secret is stored
        (the secret itself is never returned)."""
        return _spotify_status()

    @app.post("/spotify")
    def set_spotify(payload: dict = Body(...)) -> dict:  # noqa: B008 (FastAPI body param)
        """Save the Spotify Client ID + Client Secret to the gitignored auth file
        (secret from the body, never the URL; only overwritten when non-empty)."""
        from ..auth import save_auth_entry

        save_auth_entry(
            "spotify",
            endpoint=(payload.get("client_id") or ""),
            token=(payload.get("client_secret") or None),
        )
        return _spotify_status()

    @app.post("/spotify/test")
    def spotify_test() -> dict:
        """Verify the stored credentials by fetching an app token (Client Credentials)."""
        from ..spotify import from_auth

        conn = from_auth()
        if not conn.configured:
            return {"ok": False, "detail": "no credentials saved"}
        try:
            conn.token()
        except Exception as exc:  # noqa: BLE001 — surface the reason to the UI
            return {"ok": False, "detail": str(exc)}
        return {"ok": True}

    @app.get("/audio/{clip_id}")
    def audio(clip_id: str) -> Response:
        clip = store.audio.get(clip_id)
        if clip is None:
            raise HTTPException(status_code=404, detail="unknown clip")
        return Response(content=clip.data, media_type=clip.media_type)

    @app.get("/", response_class=HTMLResponse)
    def index() -> HTMLResponse:
        # no-store so a running session always gets the latest UI (new controls
        # like Demo Mode) on refresh, never a cached older page.
        return HTMLResponse(
            _render_page(store),
            headers={"Cache-Control": "no-store, must-revalidate"},
        )

    return app


def _render_page(store: _State) -> str:
    """The Tufte player page. Static: the browser polls /genmusic and /plan and
    plays the generative music with Strudel, crossfading as programs change."""
    return _PLAYER_HTML


# Loaded once. The page fetches /plan (news) and /genmusic (Strudel program text)
# on an interval; a start button satisfies the browser's audio-gesture rule, then
# each changed program is evaluate()'d (its built-in .fadeIn crossfades the swap).
# An incidental canvas visualizer reflects intensity + brainwave band.
_PLAYER_HTML = r"""<!doctype html><meta charset='utf-8'>
<meta name='viewport' content='width=device-width, initial-scale=1'>
<title>State Media FM</title>
<style>
  body{max-width:44rem;margin:6vh auto;padding:0 1.25rem;
       font:16px/1.55 Georgia,'Times New Roman',serif;color:#111;background:#fffff8}
  h1{font-weight:normal;letter-spacing:.02em;margin:0}
  h2{font-weight:normal;font-size:1.05rem;margin:.2rem 0}
  .muted{color:#666;font-size:.85rem;font-style:italic}
  button{font:inherit;padding:.5rem 1rem;margin:1rem 0;cursor:pointer;
         background:#111;color:#fffff8;border:0;border-radius:2px}
  button[disabled]{opacity:.6;cursor:default}
  #modelwrap,#tuningwrap,#quietwrap,#intensitywrap{display:inline-block;margin-left:1rem}
  #intensity{vertical-align:middle;width:6rem}
  select{font:inherit;font-size:.85rem;margin-left:.35rem}
  #tabs{margin:.3rem 0 1rem;border-bottom:1px solid #ccc}
  #tabs a{cursor:pointer;display:inline-block;padding:.3rem .7rem;margin-right:.2rem;
          color:#666;border-bottom:2px solid transparent}
  #tabs a.active{color:#111;border-bottom-color:#111}
  .authrow{margin:.4rem 0;padding:.6rem 0;border-top:1px solid #eee}
  .authrow input{font:inherit;font-size:.9rem;display:block;width:100%;max-width:26rem;margin:.2rem 0;
                 padding:.3rem;border:1px solid #ccc;border-radius:2px;background:#fffff8;color:inherit}
  .authrow button{margin-top:.2rem}
  .authrow select{margin-left:0}
  /* Collapsible Settings sections. */
  details.section{border-top:1px solid #ddd;margin:.2rem 0}
  details.section>summary{cursor:pointer;font-weight:bold;font-variant:small-caps;
    letter-spacing:.04em;padding:.6rem .1rem;list-style:none;user-select:none}
  details.section>summary::-webkit-details-marker{display:none}
  details.section>summary::before{content:'\25B8\00a0';color:#999}
  details.section[open]>summary::before{content:'\25BE\00a0'}
  details.section>*:not(summary){margin-left:.3rem}
  @media(prefers-color-scheme:dark){details.section{border-color:#333}}
  /* Toggle switch (Demo Mode). */
  .switch{display:inline-flex;align-items:center;gap:.6rem;cursor:pointer;font-style:normal}
  .switch input{position:absolute;opacity:0;width:0;height:0}
  .switch .track{position:relative;width:2.6rem;height:1.4rem;border-radius:999px;background:#ccc;
                 transition:background .15s;flex:none}
  .switch .track::after{content:'';position:absolute;top:.15rem;left:.15rem;width:1.1rem;height:1.1rem;
                 border-radius:50%;background:#fff;transition:transform .15s;box-shadow:0 1px 2px rgba(0,0,0,.3)}
  .switch input:checked + .track{background:#3a7}
  .switch input:checked + .track::after{transform:translateX(1.2rem)}
  .switch input:focus-visible + .track{outline:2px solid #3a7;outline-offset:2px}
  .chip{font:inherit;font-size:.8rem;padding:.2rem .5rem;margin:.15rem .3rem .15rem 0;
        background:transparent;color:inherit;border:1px solid #bbb;border-radius:999px}
  .srcrow{display:flex;align-items:baseline;gap:.5rem;margin:.3rem 0;padding:.35rem 0;
          border-top:1px solid #eee}
  .srcrow .grow{flex:1} .srcrow .kind{font-variant:small-caps;color:#666}
  .srcrow button{margin:0;padding:.25rem .6rem;font-size:.8rem;background:transparent;
                 color:inherit;border:1px solid #bbb;border-radius:2px}
  #newsbadge{margin-left:1rem}
  @media(prefers-color-scheme:dark){
    #tabs{border-color:#333} #tabs a.active{color:#eee;border-bottom-color:#eee}
    .authrow{border-color:#333} .authrow input{background:#111;color:#eee;border-color:#444}
    .srcrow{border-color:#333} .chip,.srcrow button{border-color:#555}
    .switch .track{background:#444}}
  #viz{display:block;width:100%;height:64px;margin:.5rem 0}
  article{border-top:1px solid #ccc;padding-top:.6rem;margin-top:1rem}
  .newslist{margin:.4rem 0;padding-left:1.2rem}
  .newslist li{margin:.25rem 0}
  .newslist a{color:inherit;text-decoration:none;border-bottom:1px solid #bbb}
  .newslist a:hover{border-bottom-color:currentColor}
  audio{width:100%;margin:.4rem 0}
  @media(prefers-color-scheme:dark){
    body{background:#111;color:#eee}.muted{color:#aaa}
    button{background:#eee;color:#111}article{border-color:#333}}
</style>
<h1>State Media FM</h1>
<nav id='tabs'><a data-tab='player' class='active'>Player</a><a data-tab='settings'>Settings</a></nav>
<div id='player-view'>
<p class='muted' id='status'>internal radio · press play to begin</p>
<button id='play'>▶ Start radio</button>
<button id='stopbtn'>■ Stop broadcast</button>
<label class='muted' id='quietwrap'><input type='checkbox' id='quiet'> quiet mode</label>
<span class='muted' id='newsbadge'></span>
<canvas id='viz'></canvas>
<section id='news'><p class='muted'>Loading…</p></section>
</div>
<div id='settings-view' hidden>
  <!-- Demo Mode: always at the very top -->
  <div class='authrow' id='demo-row'>
    <label class='switch'><input type='checkbox' id='demo'><span class='track'></span>
      <strong>Demo Mode</strong></label>
    <span class='muted' id='demo-status'></span>
  </div>
  <p class='muted'>Reads the Hacker News front page and a repo's git issues every 5
  minutes, music in between. Turning it on adds those two sources; off removes them.</p>

  <details class='section' open>
    <summary>Sources</summary>
    <p class='muted'>Which activity State Media FM airs. Changes apply to the running
    session (not written to the config file).</p>
    <div id='sourcelist'></div>
    <div class='authrow'>
      <select id='src-kind'></select>
      <input id='src-topic' placeholder='topic (optional)'>
      <input id='src-param' placeholder='—'>
      <input id='src-maxage' placeholder='max age (default 12h — recent updates only)' hidden>
      <input id='src-every' placeholder='every (e.g. 15m)' value='15m'>
      <input id='src-headlines' type='number' min='1' placeholder='headlines (max read)'>
      <input id='src-maxcount' type='number' min='1' placeholder='max_count (items polled)'>
      <input id='src-offset' placeholder='offset (e.g. 0, 5m)'>
      <button id='src-add'>Add source</button>
      <span class='muted' id='src-status'></span>
    </div>
  </details>

  <details class='section'>
    <summary>Auth</summary>
    <p class='muted'>Personal endpoints and tokens for the activity sources State Media
    FM polls (GitHub, GitLab, Jira, Slack, PagerDuty). Stored locally in a gitignored
    file (<code>statemediafm.auth.toml</code>, owner-only); tokens are masked here and
    never committed or sent anywhere but your own server.</p>
    <div id='authform'></div>
  </details>

  <details class='section'>
    <summary>Gateways</summary>
    <p class='muted'>Model/LLM gateways used for news parsing — each has a slot for its
    <strong>URL</strong> (base endpoint) and <strong>auth token</strong> (API key).
    Provider-agnostic: LiteLLM, OpenRouter, Azure OpenAI, a self-hosted vLLM/Ollama/
    NIM, etc. Stored in the same gitignored auth file; tokens masked, never sent
    anywhere but your own server.</p>
    <div id='gatewayform'></div>
    <p class='muted'>Quick-fill from a provider preset (sets the URL slot above and
    suggests a news model — you still enter the API key in the token slot):</p>
    <div id='presets'></div>
  </details>

  <details class='section'>
    <summary>Narration</summary>
    <div class='authrow'>
      <label class='muted' id='modelwrap'>ambient generator
        <select id='model'></select>
      </label>
      <label class='muted' id='tuningwrap'>tuning A=
        <select id='tuning'></select>
      </label>
      <label class='muted' id='intensitywrap'>energy
        <input type='range' id='intensity' min='0' max='1' step='0.05'>
        <span id='intensity-band'></span>
      </label>
    </div>
    <h3>Mix</h3>
    <p class='muted'>Mix ambient generator <em>types</em> instead of a single one — the
    station rotates through the selected generators (~6&nbsp;min each). Optionally mix
    Spotify songs into the song slots.</p>
    <div class='authrow'>
      <label class='muted'><input type='checkbox' id='mix-gen'> Mix ambient generators</label>
      <span class='muted' id='mix-models'></span>
    </div>
    <div class='authrow'>
      <label class='muted'><input type='checkbox' id='mix-spotify'> Mix in Spotify songs</label>
      <span class='muted' id='mix-spotify-hint'></span>
    </div>
    <p class='muted'>Pick a themed <strong>persona</strong> (a writing-style + voice +
    station-phrasing bundle), or <em>Custom</em> to set the style and voice yourself.
    Applies to the next news cycle.</p>
    <div class='authrow'>
      <label class='muted'>persona <select id='persona-sel'></select></label>
      <span class='muted' id='persona-lock'></span>
    </div>
    <div class='authrow'>
      <label class='muted'>style
        <input id='style-input' list='style-list' placeholder='e.g. bbc-world'>
        <datalist id='style-list'></datalist>
      </label>
      <label class='muted'>voice <select id='voice-sel'></select></label>
      <button id='narration-save'>Apply</button>
      <span class='muted' id='narration-status'></span>
    </div>
    <h3>Spotify</h3>
    <p class='muted'>Connect Spotify to resolve song slots to tracks. Create an app at
    <code>developer.spotify.com</code> and paste its Client ID + Client Secret —
    stored locally in the gitignored auth file, the secret masked and never sent
    anywhere but your own server.</p>
    <div class='authrow'>
      <input id='sp-id' placeholder='Client ID'>
      <input id='sp-secret' type='password' autocomplete='off' placeholder='Client Secret'>
      <button id='sp-save'>Save</button>
      <button id='sp-test'>Test connection</button>
      <span class='muted' id='sp-status'></span>
    </div>
    <div id='newsmodel-wrap' hidden>
      <h3>News-parsing model</h3>
      <p class='muted'>Which model on the <code>llm-gateway</code> writes the news.
      Pick one the gateway serves, or type a model string. Applies to the next news
      cycle.</p>
      <div class='authrow'>
        <select id='newsmodel'></select>
        <input id='newsmodel-custom' placeholder='or type a model, e.g. openai/gpt-4o-mini'>
        <input id='newsmodel-temp' type='number' step='0.1' min='0' max='2' placeholder='temperature'>
        <input id='newsmodel-maxtokens' type='number' min='1' placeholder='max_tokens'>
        <button id='newsmodel-save'>Set model</button>
        <button id='newsmodel-discover'>↻ Discover from gateway</button>
        <span class='muted' id='newsmodel-status'></span>
      </div>
    </div>
  </details>

  <details class='section'>
    <summary>Commercial Features</summary>
    <p class='muted'>Commercial modules (e.g. themed voice personas) unlock with a
    license key — stored locally in a gitignored file, owner-only, never sent
    anywhere but your own server.</p>
    <div class='authrow' id='license-row'>
      <input id='license-key' type='password' autocomplete='off' placeholder='license key'>
      <button id='license-save'>Unlock</button>
      <span class='muted' id='license-status'></span>
    </div>
    <div id='license-modules'></div>
  </details>
</div>

<script src='https://unpkg.com/@strudel/web@1.0.3'></script>
<script>
const statusEl=document.getElementById('status');
const newsEl=document.getElementById('news');
const btn=document.getElementById('play');
const modelSel=document.getElementById('model');
const stopBtn=document.getElementById('stopbtn');

// Stop/resume the whole broadcast — pauses the server refresh loop (no polling/
// TTS/LLM) and silences the audio; resuming restores both.
let broadcasting=true;
function updateStopBtn(){ stopBtn.textContent = broadcasting ? '■ Stop broadcast' : '▶ Resume broadcast'; }
async function loadBroadcast(){
  try{ broadcasting=(await (await fetch('/broadcast')).json()).broadcasting; updateStopBtn(); }catch(e){}
}
stopBtn.addEventListener('click', async ()=>{
  broadcasting=!broadcasting; updateStopBtn();
  try{ await fetch('/broadcast?on='+(broadcasting?'true':'false'), {method:'POST'}); }catch(e){}
  await pollMusic();
});

// Populate the ambient-generator dropdown and switch models on change.
async function loadModels(){
  try{
    const d=await (await fetch('/models')).json();
    // The ambient-generator picker (shown by default; hide via [genmusic] selector=false).
    document.getElementById('modelwrap').style.display = d.selector ? 'inline-block' : 'none';
    modelSel.innerHTML='';
    for(const m of d.models){
      const o=document.createElement('option'); o.value=m; o.textContent=m;
      if(m===d.current) o.selected=true; modelSel.appendChild(o);
    }
  }catch(e){}
}
modelSel.addEventListener('change', async ()=>{
  try{
    await fetch('/model?name='+encodeURIComponent(modelSel.value), {method:'POST'});
    lastProgram='';           // force a re-evaluate of the new model's program
    await pollMusic();
  }catch(e){}
});

// Concert-A tuning selector (440 / 435 / 433 Hz) — retunes all notes.
const tuningSel=document.getElementById('tuning');
async function loadTunings(){
  try{
    const d=await (await fetch('/tuning')).json();
    tuningSel.innerHTML='';
    for(const t of d.tunings){
      const o=document.createElement('option'); o.value=t; o.textContent=t+' Hz';
      if(t===d.current) o.selected=true; tuningSel.appendChild(o);
    }
  }catch(e){}
}
tuningSel.addEventListener('change', async ()=>{
  try{
    await fetch('/tuning?a='+encodeURIComponent(tuningSel.value), {method:'POST'});
    lastProgram=''; await pollMusic();
  }catch(e){}
});

// Quiet mode — music only around the news, silent between.
const quietBox=document.getElementById('quiet');
async function loadQuiet(){
  try{ const d=await (await fetch('/quiet')).json(); quietBox.checked=!!d.quiet_mode; }catch(e){}
}
quietBox.addEventListener('change', async ()=>{
  try{ await fetch('/quiet?on='+(quietBox.checked?'true':'false'), {method:'POST'}); await pollMusic(); }catch(e){}
});

// Demo Mode — earlier-milestone feel: HN + git issues every 5 min, music between.
const demoBox=document.getElementById('demo');
const demoStatus=document.getElementById('demo-status');
async function loadDemo(){
  try{ const d=await (await fetch('/demo')).json();
    demoBox.checked=!!d.demo_mode;
    demoStatus.textContent=d.demo_mode?'on · reading every 5 min':'';
  }catch(e){}
}
demoBox.addEventListener('change', async ()=>{
  const on=demoBox.checked;
  demoStatus.textContent=on?'starting…':'';
  try{
    const d=await (await fetch('/demo?on='+(on?'true':'false'), {method:'POST'})).json();
    demoStatus.textContent=d.demo_mode?'on · reading every 5 min':'';
    if(quietBox && d.demo_mode) quietBox.checked=false;  // demo keeps music continuous
    await loadSources();
  }catch(e){ demoStatus.textContent='could not toggle'; }
});

// Base energy — the brainwave level a session starts at; news lifts it.
const intensityEl=document.getElementById('intensity');
const intensityBand=document.getElementById('intensity-band');
async function loadIntensity(){
  try{ const d=await (await fetch('/intensity')).json();
    intensityEl.value=d.current; intensityBand.textContent=d.band||''; }catch(e){}
}
intensityEl.addEventListener('change', async ()=>{
  try{ const d=await (await fetch('/intensity?level='+encodeURIComponent(intensityEl.value), {method:'POST'})).json();
    intensityBand.textContent=d.band||''; lastProgram=''; await pollMusic();
  }catch(e){}
});
let started=false, lastProgram='', currentProg='', ducked=false, viz={intensity:0, band:'theta', on:false};
const newsPlayer=new Audio(); let lastNewsUrl='';

// Ducking — the radio-production principles applied within the browser's limits.
//   DEPTH: shallow, ~6-9 dB (gain 0.45 ≈ -7 dB), not the -12/-15 dB that makes
//     the bed feel like it left the room.
//   ATTACK fast (immediate on the first syllable), RELEASE slow and musical: the
//     bed swells back ~600 ms AFTER the last word, not under it — the news tail is
//     faded so it tapers into the returning music (never a hard stop = an exit).
//   NEVER TO SILENCE: the bed keeps playing under the voice; releases overlap.
// Honest limits of @strudel/web 1.0.3: gain is set by re-evaluating the pattern
// (no master-gain automation, and a re-eval mid-note glitches), so a true ramped
// or midrange-only (1-4 kHz) sidechain isn't possible here — those, plus on-air
// processor AGC compensation, await a server-side mix. We do the shallow full-band
// duck + a faded, delayed release, which is the audible 80%.
const DUCK={GAIN:0.45, RELEASE_MS:600, NEWS_FADE_MS:500};
let releaseTimer=null;
async function playCurrent(){
  if(!currentProg) return;
  const base=currentProg.replace(/\.fadeIn\([0-9.]+\)\s*$/,'');
  const code=ducked?base+'.gain('+DUCK.GAIN+')':currentProg;
  // evaluate() is async; await it so a rejection is caught here (not "uncaught").
  try{ await evaluate(code); }
  catch(e){ console.error('strudel:',e); statusEl.textContent='music error: '+((e&&e.message)||e); }
}
function setDuck(on){ if(started && ducked!==on){ ducked=on; playCurrent(); } }
// Fade the news element's tail over NEWS_FADE_MS so the voice tapers out.
function fadeNewsOut(){
  const steps=10, dt=DUCK.NEWS_FADE_MS/steps; let v=newsPlayer.volume;
  const iv=setInterval(()=>{ v-=1/steps; if(v<=0){ newsPlayer.volume=0; clearInterval(iv); }
    else newsPlayer.volume=v; }, dt);
}
newsPlayer.addEventListener('play', ()=>{
  if(releaseTimer){ clearTimeout(releaseTimer); releaseTimer=null; }
  newsPlayer.volume=1; setDuck(true);   // fast attack, full voice
});
function scheduleRelease(){
  // Slow, musical release: hold the (shallow) duck a beat, let the bed swell back.
  if(releaseTimer) clearTimeout(releaseTimer);
  releaseTimer=setTimeout(()=>{ setDuck(false); releaseTimer=null; }, DUCK.RELEASE_MS);
}
// Near the end, taper the voice; on end/pause, release after the overlap window.
newsPlayer.addEventListener('timeupdate', ()=>{
  if(newsPlayer.duration && newsPlayer.duration-newsPlayer.currentTime<=DUCK.NEWS_FADE_MS/1000
     && newsPlayer.volume>0.99) fadeNewsOut();
});
newsPlayer.addEventListener('ended', scheduleRelease);
newsPlayer.addEventListener('pause', scheduleRelease);

let musicSilenced=false;
async function pollMusic(){
  try{
    const d=await (await fetch('/genmusic')).json();
    // Gate: silence when the server says not to play (broadcast stopped, or quiet).
    if(started && d.play===false){
      if(!musicSilenced){ try{ await evaluate('silence'); }catch(e){} musicSilenced=true; }
      viz.on=false;
      statusEl.textContent = broadcasting ? '● quiet · silent (music returns before the news)'
                                          : '■ broadcast stopped';
      return;
    }
    if(!d.text){ statusEl.textContent='waiting for activity…'; return; }
    viz.intensity=d.intensity; viz.band=d.brainwave_band; viz.on=started;
    // (re)start when the program changes OR the gate just re-opened after silence
    if(started && (d.text!==lastProgram || musicSilenced)){
      lastProgram=d.text; currentProg=d.text; musicSilenced=false; await playCurrent();
    }
    const ctx=(typeof getAudioContext==='function')?getAudioContext():null;
    const ac=ctx?(' · audio '+ctx.state):'';
    statusEl.textContent=(started?(ducked?'● news over music':'● on air'):'ready')+
      ' · '+d.style+' · '+d.brainwave_band+' · intensity '+d.intensity.toFixed(2)+ac;
  }catch(e){}
}
async function pollNews(){
  try{
    const d=await (await fetch('/plan')).json();
    const segs=d.segments||[]; const seen=new Set(); let html='';
    for(const s of segs){
      if(seen.has(s.title)) continue; seen.add(s.title);
      const hs=s.headlines||[];
      // The news items as a linked list (each links to its source); fall back to
      // the spoken prose if a segment has no discrete headlines.
      const body = hs.length
        ? '<ul class="newslist">'+hs.map(h=>'<li>'+(h.url
            ? '<a href="'+esc(h.url)+'" target="_blank" rel="noopener noreferrer">'+esc(h.title)+'</a>'
            : esc(h.title))+'</li>').join('')+'</ul>'
        : '<p>'+esc(s.script||'')+'</p>';
      html+='<article><h2>'+esc(s.title||'News')+'</h2>'+body+
            (s.audio_url?'<audio controls src="'+esc(s.audio_url)+'"></audio>':'')+'</article>';
    }
    newsEl.innerHTML=html||'<p class="muted">No broadcast yet.</p>';
    const first=segs.find(s=>s.audio_url);
    if(started && first && first.audio_url!==lastNewsUrl){
      lastNewsUrl=first.audio_url; newsPlayer.src=first.audio_url;
      newsPlayer.play().catch(e=>console.warn('news play:',e));
    }
  }catch(e){}
}
btn.addEventListener('click', async ()=>{
  if(started) return; started=true; btn.disabled=true; btn.textContent='● On air';
  statusEl.textContent='starting…';
  try{ await initStrudel(); }
  catch(e){ console.error(e); statusEl.textContent='init error: '+((e&&e.message)||e); return; }
  // Warm up: the first evaluate can reject with "setcps is not defined" until
  // Strudel finishes registering its runtime. Retry a tiny silent pattern until
  // it succeeds, THEN play the real program.
  statusEl.textContent='warming up…';
  for(let i=0;i<80;i++){
    try{ await evaluate('setcps(0.5)\ns("~")'); break; }
    catch(e){ await new Promise(r=>setTimeout(r,80)); }
  }
  if(typeof window.samples==='function'){
    samples('github:tidalcycles/dirt-samples').catch(e=>console.warn('samples failed:',e));
  }
  await pollMusic();
  pollNews();
});
// Tabs: Player / Settings.
function esc(s){ return String(s==null?'':s).replace(/[&<>"]/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c])); }
document.querySelectorAll('#tabs a').forEach(a=>a.addEventListener('click', ()=>{
  document.querySelectorAll('#tabs a').forEach(x=>x.classList.toggle('active', x===a));
  const tab=a.dataset.tab;
  document.getElementById('player-view').hidden = tab!=='player';
  document.getElementById('settings-view').hidden = tab!=='settings';
  if(tab==='settings'){ loadDemo(); loadSources(); loadNarration(); loadMix(); loadSpotify(); loadNewsModel(); loadPresets(); loadAuth(); loadGateways(); loadLicense(); }
}));

// ── Narration: persona (style+voice+phrasing) or Custom style + voice ─────────
const personaSel=document.getElementById('persona-sel');
async function loadNarration(){
  try{
    const p=await (await fetch('/persona')).json();
    const licensed = !!p.licensed;
    personaSel.innerHTML='';
    for(const x of ['Custom', ...(p.personas||[])]){
      const o=document.createElement('option'); o.value=x; o.textContent=x;
      // Personas are a commercial module: lock them until licensed.
      if(x!=='Custom' && !licensed){ o.disabled=true; o.textContent=x+' 🔒'; }
      if(x===p.current) o.selected=true; personaSel.appendChild(o);
    }
    document.getElementById('persona-lock').textContent =
      licensed ? '' : '· commercial module — unlock under Commercial Features';
    const custom = (p.current||'Custom')==='Custom';
    // Custom → the style/voice fields are yours to set; a persona drives them.
    document.getElementById('style-input').disabled = !custom;
    document.getElementById('voice-sel').disabled = !custom;
    const s=await (await fetch('/style')).json();
    const inp=document.getElementById('style-input'); inp.value=s.current||'';
    const dl=document.getElementById('style-list'); dl.innerHTML='';
    for(const x of (s.suggestions||[])){ const o=document.createElement('option'); o.value=x; dl.appendChild(o); }
    const v=await (await fetch('/voice')).json();
    const sel=document.getElementById('voice-sel'); sel.innerHTML='';
    for(const x of (v.voices||[])){ const o=document.createElement('option'); o.value=x; o.textContent=x;
      if(x===v.current) o.selected=true; sel.appendChild(o); }
  }catch(e){}
}
personaSel.addEventListener('change', async ()=>{
  const st=document.getElementById('narration-status'); st.textContent='saving…';
  try{
    const r=await fetch('/persona?name='+encodeURIComponent(personaSel.value), {method:'POST'});
    if(!r.ok){ const e=await r.json().catch(()=>({}));
      st.textContent = r.status===402 ? 'locked — add a license key' : ('error: '+(e.detail||r.status));
      return; }
    await loadNarration(); loadNewsBadge();
    st.textContent = personaSel.value==='Custom' ? 'custom' : ('persona: '+personaSel.value+' (next cycle)');
  }catch(e){ st.textContent='error'; }
});
document.getElementById('license-save').addEventListener('click', async ()=>{
  const st=document.getElementById('license-status'); st.textContent='checking…';
  const key=document.getElementById('license-key').value.trim(); if(!key) return;
  try{
    const r=await fetch('/license',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({key})});
    const d=await r.json().catch(()=>({}));
    if(!r.ok){ st.textContent='error: '+(d.detail||r.status); return; }
    const ok=(d.modules||[]).some(m=>m.entitled);
    st.textContent = ok ? 'unlocked' : 'key saved, but no modules unlocked';
    document.getElementById('license-key').value='';
    await loadNarration(); await loadLicense();
  }catch(e){ st.textContent='error'; }
});
// Mix (under Narration) — rotate ambient generators, and/or mix in Spotify songs.
async function loadMix(){
  try{
    const d=await (await fetch('/mix')).json();
    document.getElementById('mix-gen').checked=!!d.mix_generators;
    document.getElementById('mix-spotify').checked=!!d.mix_spotify;
    document.getElementById('mix-spotify-hint').textContent =
      d.spotify_configured ? '' : '· connect Spotify (below) first';
    const wrap=document.getElementById('mix-models'); wrap.innerHTML='';
    const sel=new Set(d.selected||[]);
    for(const m of (d.models||[])){
      const lab=document.createElement('label'); lab.className='muted'; lab.style.marginLeft='.7rem';
      lab.innerHTML='<input type="checkbox" '+(sel.has(m)?'checked':'')+' value="'+esc(m)+'"> '+esc(m);
      wrap.appendChild(lab);
    }
    wrap.style.display = d.mix_generators ? 'inline' : 'none';
  }catch(e){}
}
async function saveMix(){
  const selected=[...document.querySelectorAll('#mix-models input:checked')].map(c=>c.value);
  const body={mix_generators:document.getElementById('mix-gen').checked,
              mix_spotify:document.getElementById('mix-spotify').checked, selected};
  try{ await fetch('/mix',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify(body)}); await loadMix(); }catch(e){}
}
document.getElementById('mix-gen').addEventListener('change', saveMix);
document.getElementById('mix-spotify').addEventListener('change', saveMix);
document.getElementById('mix-models').addEventListener('change', saveMix);

// Spotify connector (under Narration) — Client ID + Secret, saved gitignored.
async function loadSpotify(){
  try{
    const d=await (await fetch('/spotify')).json();
    document.getElementById('sp-id').value=d.client_id||'';
    document.getElementById('sp-status').textContent =
      d.configured ? 'connected · secret set' : (d.secret_set ? 'secret set — add Client ID' : 'not connected');
  }catch(e){}
}
document.getElementById('sp-save').addEventListener('click', async ()=>{
  const st=document.getElementById('sp-status'); st.textContent='saving…';
  const body={client_id:document.getElementById('sp-id').value.trim(),
              client_secret:document.getElementById('sp-secret').value};
  try{
    await fetch('/spotify',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    document.getElementById('sp-secret').value='';
    await loadSpotify();
  }catch(e){ st.textContent='error'; }
});
document.getElementById('sp-test').addEventListener('click', async ()=>{
  const st=document.getElementById('sp-status'); st.textContent='testing…';
  try{
    const d=await (await fetch('/spotify/test',{method:'POST'})).json();
    st.textContent = d.ok ? 'connection OK ✓' : ('failed: '+(d.detail||'unknown'));
  }catch(e){ st.textContent='error'; }
});
// Commercial Features — the registered modules and whether each is unlocked.
async function loadLicense(){
  try{
    const d=await (await fetch('/license')).json();
    const wrap=document.getElementById('license-modules');
    wrap.innerHTML=(d.modules||[]).map(m=>
      '<div class="srcrow"><span class="grow">'+esc(m.name)+' — '+esc(m.description||'')+
      '</span><span class="kind">'+(m.entitled?'✓ unlocked':'🔒 locked')+'</span></div>').join('')
      || '<p class="muted">No commercial modules registered.</p>';
  }catch(e){}
}
document.getElementById('narration-save').addEventListener('click', async ()=>{
  const st=document.getElementById('narration-status'); st.textContent='saving…';
  const style=document.getElementById('style-input').value.trim();
  const voice=document.getElementById('voice-sel').value;
  try{
    if(style){ const r=await fetch('/style?name='+encodeURIComponent(style),{method:'POST'});
      if(!r.ok){ st.textContent='style error'; return; } }
    if(voice){ const r=await fetch('/voice?name='+encodeURIComponent(voice),{method:'POST'});
      if(!r.ok){ st.textContent='voice error'; return; } }
    st.textContent='applied (next cycle)';
  }catch(e){ st.textContent='error'; }
});

// ── Live source management ───────────────────────────────────────────────────
// The add menu leads with GitHub/GitLab work-item URLs (both the forge `repo`
// kind — a repo URL or a pasted issue/PR/MR URL), then the other sources. Each
// option names its one extra parameter and a placeholder.
const ADD_OPTIONS=[
  {label:'GitHub work items (URL)', kind:'repo', key:'repo',
   ph:'https://github.com/owner/repo  (or an issue/PR URL)'},
  {label:'GitLab work items (URL)', kind:'repo', key:'repo',
   ph:'https://gitlab.com/group/project  (or an issue/MR URL)'},
  {label:'Hacker News', kind:'hackernews', key:null, ph:null},
  {label:'Slack channel', kind:'slack', key:'channel', ph:'channel name or ID'},
  {label:'Jira project', kind:'jira', key:'project', ph:'project key, e.g. OPS'},
  {label:'PagerDuty', kind:'pagerduty', key:'statuses',
   ph:'statuses (comma-sep), e.g. triggered,acknowledged'},
];
const srcKind=document.getElementById('src-kind');
const srcParam=document.getElementById('src-param');
let addOptions=[];  // ADD_OPTIONS plus any extra server kinds (e.g. plugins)
function currentAddOption(){ return addOptions[srcKind.value] || {}; }
async function loadSources(){
  const list=document.getElementById('sourcelist');
  try{
    const d=await (await fetch('/sources')).json();
    if(srcKind.options.length===0){
      const covered=new Set(['hn','repo','hackernews','slack','jira','pagerduty']);
      const extras=(d.kinds||[]).filter(k=>!covered.has(k)).map(k=>({label:k, kind:k, key:null, ph:null}));
      addOptions=ADD_OPTIONS.concat(extras);
      addOptions.forEach((opt,i)=>{ const o=document.createElement('option'); o.value=i; o.textContent=opt.label; srcKind.appendChild(o); });
      updateSrcPlaceholder();
    }
    list.innerHTML='';
    for(const s of (d.sources||[])){
      const row=document.createElement('div'); row.className='srcrow';
      const cfg=s.config||{};
      const extra=[cfg.headlines!=null?('headlines '+cfg.headlines):'',
                   cfg.max_count!=null?('max '+cfg.max_count):'',
                   cfg.max_age!=null?('≤'+cfg.max_age):''].filter(Boolean).join(' · ');
      row.innerHTML='<span class="kind">'+esc(s.kind||'?')+'</span>'+
        '<span class="grow">'+esc(s.topic||'')+' <span class="muted">· every '+esc(s.every)+
        (extra?(' · '+esc(extra)):'')+'</span></span>'+
        '<button>Remove</button>';
      row.querySelector('button').addEventListener('click', async ()=>{
        try{ await fetch('/sources/'+s.index,{method:'DELETE'}); await loadSources(); }catch(e){}
      });
      list.appendChild(row);
    }
    if(!(d.sources||[]).length) list.innerHTML='<p class="muted">No sources yet.</p>';
  }catch(e){ list.textContent='Could not load sources.'; }
}
function updateSrcPlaceholder(){
  const opt=currentAddOption();
  srcParam.placeholder = opt.ph || '—'; srcParam.style.display = opt.key ? '' : 'none';
  // max_age only applies to the GitHub/GitLab work-item (forge) sources.
  document.getElementById('src-maxage').hidden = opt.kind!=='repo';
}
srcKind.addEventListener('change', updateSrcPlaceholder);
document.getElementById('src-add').addEventListener('click', async ()=>{
  const st=document.getElementById('src-status'); const opt=currentAddOption();
  const seg={source:opt.kind};
  if(opt.key){
    const v=srcParam.value.trim(); if(!v){ st.textContent='needs '+opt.ph; return; }
    seg[opt.key] = opt.kind==='pagerduty' ? v.split(',').map(x=>x.trim()).filter(Boolean) : v;
  }
  const val=id=>document.getElementById(id).value.trim();
  if(val('src-topic')) seg.topic=val('src-topic');
  if(val('src-every')) seg.every=val('src-every');
  if(val('src-headlines')) seg.headlines=parseInt(val('src-headlines'),10);
  if(val('src-maxcount')) seg.max_count=parseInt(val('src-maxcount'),10);
  if(val('src-offset')) seg.offset=val('src-offset');
  if(opt.kind==='repo' && val('src-maxage')) seg.max_age=val('src-maxage');
  st.textContent='adding…';
  try{
    const r=await fetch('/sources',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(seg)});
    if(!r.ok){ const e=await r.json().catch(()=>({})); st.textContent='error: '+(e.detail||r.status); return; }
    for(const id of ['src-param','src-maxage','src-topic','src-headlines','src-maxcount','src-offset'])
      document.getElementById(id).value='';
    st.textContent=''; await loadSources();
  }catch(e){ st.textContent='error'; }
});

// ── LLM gateway presets ──────────────────────────────────────────────────────
async function loadPresets(){
  const wrap=document.getElementById('presets');
  try{
    const d=await (await fetch('/llm-presets')).json();
    wrap.innerHTML='';
    for(const p of (d.presets||[])){
      const b=document.createElement('button'); b.className='chip'; b.textContent=p.name;
      b.title='endpoint '+(p.api_base||'(set yours)')+' · model '+p.model;
      b.addEventListener('click', ()=>{
        const ep=document.querySelector('.authrow[data-source="llm-gateway"] .ep');
        if(ep) ep.value=p.api_base;
        newsModelCustom.value=p.model;
        document.getElementById('newsmodel-status').textContent='preset: '+p.name+' — enter the API key in the token slot above, then Save.';
      });
      wrap.appendChild(b);
    }
  }catch(e){}
}

// News-parsing model selector (Settings) — only shown when the server runs live.
const newsModelSel=document.getElementById('newsmodel');
const newsModelCustom=document.getElementById('newsmodel-custom');
async function loadNewsModel(){
  try{
    const d=await (await fetch('/news-model')).json();
    document.getElementById('newsmodel-wrap').hidden = !d.live;
    if(!d.live) return;
    newsModelSel.innerHTML='';
    for(const m of (d.models||[])){
      const o=document.createElement('option'); o.value=m; o.textContent=m;
      if(m===d.current) o.selected=true; newsModelSel.appendChild(o);
    }
    document.getElementById('newsmodel-temp').value = d.temperature!=null ? d.temperature : '';
    document.getElementById('newsmodel-maxtokens').value = d.max_tokens!=null ? d.max_tokens : '';
    document.getElementById('newsmodel-status').textContent='current: '+esc(d.current||'');
  }catch(e){}
}
document.getElementById('newsmodel-save').addEventListener('click', async ()=>{
  const name=(newsModelCustom.value.trim())||newsModelSel.value;
  if(!name) return;
  const st=document.getElementById('newsmodel-status'); st.textContent='saving…';
  let q='/news-model?name='+encodeURIComponent(name);
  const t=document.getElementById('newsmodel-temp').value.trim();
  const mt=document.getElementById('newsmodel-maxtokens').value.trim();
  if(t!=='') q+='&temperature='+encodeURIComponent(t);
  if(mt!=='') q+='&max_tokens='+encodeURIComponent(mt);
  try{
    const r=await fetch(q, {method:'POST'});
    if(!r.ok){ const e=await r.json().catch(()=>({})); st.textContent='error: '+(e.detail||r.status); return; }
    newsModelCustom.value=''; await loadNewsModel(); loadNewsBadge();
  }catch(e){ st.textContent='error'; }
});
// Auto-discover the gateway's model catalogue (OpenAI-compatible /models).
document.getElementById('newsmodel-discover').addEventListener('click', async ()=>{
  const st=document.getElementById('newsmodel-status'); st.textContent='discovering…';
  try{
    const r=await fetch('/news-model/discover', {method:'POST'});
    if(!r.ok){ st.textContent='error: '+r.status; return; }
    const d=await r.json(); await loadNewsModel();
    st.textContent = d.discovered && d.discovered.length
      ? ('discovered '+d.discovered.length+' models') : 'no models returned by the gateway';
  }catch(e){ st.textContent='error'; }
});
// A single endpoint/token row, shared by the Auth (news sources) and Gateways
// sections — both POST to /auth; the placeholder differs (endpoint vs URL).
function authRow(src, c, epPlaceholder){
  const row=document.createElement('div'); row.className='authrow'; row.dataset.source=src;
  row.innerHTML='<strong>'+esc(src)+'</strong> <span class="muted">'+
    (c.token_set?('· token set '+esc(c.token_hint||'')):'· no token')+'</span>'+
    '<input class="ep" placeholder="'+esc(epPlaceholder)+'" value="'+esc(c.endpoint||'')+'">'+
    '<input class="tok" type="password" autocomplete="off" placeholder="'+
      (c.token_set?'new token (blank keeps current)':'auth token')+'">'+
    '<button>Save</button>';
  const btn=row.querySelector('button');
  btn.addEventListener('click', async ()=>{
    btn.disabled=true; btn.textContent='Saving…';
    const body={source:src, endpoint:row.querySelector('.ep').value, token:row.querySelector('.tok').value};
    try{ await fetch('/auth',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
      await loadAuth(); await loadGateways();
    }catch(e){ btn.disabled=false; btn.textContent='Save'; }
  });
  return row;
}
async function loadAuth(){
  const wrap=document.getElementById('authform');
  try{
    const d=await (await fetch('/auth')).json();
    wrap.innerHTML='';
    for(const src of (d.sources||[])) wrap.appendChild(authRow(src,(d.config&&d.config[src])||{},'endpoint (optional)'));
  }catch(e){ wrap.textContent='Could not load settings.'; }
}
async function loadGateways(){
  const wrap=document.getElementById('gatewayform');
  try{
    const d=await (await fetch('/auth')).json();
    wrap.innerHTML='';
    for(const g of (d.gateways||[])) wrap.appendChild(authRow(g,(d.config&&d.config[g])||{},'URL (base endpoint)'));
  }catch(e){ wrap.textContent='Could not load gateways.'; }
}

// News-parsing badge on the Player tab: live model, or the deterministic copy.
const newsBadge=document.getElementById('newsbadge');
async function loadNewsBadge(){
  try{
    const d=await (await fetch('/news-model')).json();
    newsBadge.textContent = d.live ? ('news: '+(d.current||'live model')) : 'news: offline copy';
    newsBadge.title = d.live ? 'LLM-written via the llm-gateway' : 'deterministic, no LLM';
  }catch(e){}
}

loadModels(); loadTunings(); loadQuiet(); loadIntensity(); loadBroadcast(); loadNewsBadge(); pollMusic(); pollNews();
setInterval(pollMusic, 8000);
setInterval(pollNews, 15000);
setInterval(loadNewsBadge, 30000);

// Incidental visualizer: bars pulsing with intensity, hue by brainwave band.
const cv=document.getElementById('viz'), ctx=cv.getContext('2d');
const HUE={delta:210, theta:260, alpha:170, beta:35, gamma:0};
function resize(){ cv.width=cv.clientWidth; cv.height=64; }
addEventListener('resize', resize); resize();
let t=0;
function draw(){
  t+=0.05; const w=cv.width, h=cv.height; ctx.clearRect(0,0,w,h);
  const n=28, hue=HUE[viz.band]??260, amp=0.12+viz.intensity*0.88;
  const speed=viz.on?(0.5+viz.intensity*2.5):0.15;
  for(let i=0;i<n;i++){
    const x=(i+0.5)/n*w;
    const v=Math.abs(Math.sin(t*speed + i*0.5));
    const bh=4+v*amp*(h-8);
    ctx.fillStyle='hsl('+hue+' 60% '+(28+v*32)+'%)';
    ctx.fillRect(x-2,(h-bh)/2,4,bh);
  }
  requestAnimationFrame(draw);
}
draw();
</script>
"""
