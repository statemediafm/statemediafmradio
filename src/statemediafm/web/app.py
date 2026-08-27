"""FastAPI app for the M1 slice: /health, /plan, /audio/{id}, and a minimal page.

The web layer is deliberately thin (plan §5.6): it holds the latest plan + an
in-memory audio store and renders/serves them. It contains no summarization or
scheduling logic. FastAPI is imported lazily so the rest of the package (and the
CLI/tests) work without the web dependency installed.
"""

from __future__ import annotations

import json as _json
import secrets as _secrets
import sys as _sys
from dataclasses import dataclass

from ..core.models import AudioRef, BroadcastPlan, StrudelProgram
from ..core.plan import plan_to_dict

# ── Control-API access policy (loopback, single-operator; see SECURITY_MODEL.md) ──
#
# The server binds to loopback and is meant for one operator — but "loopback" does
# not stop a malicious web page the operator visits from firing requests at the
# port (CSRF / DNS rebinding). A :class:`SecurityPolicy` closes that gap:
#   * a per-session token, embedded in the served page, is required on every route
#     except a small public set — a cross-origin page cannot read the page body
#     (same-origin policy), so it cannot learn the token;
#   * the token rides in a custom ``X-SMFM-Token`` header, which also forces a CORS
#     preflight cross-origin — denied, since we send no permissive CORS headers;
#   * a Host/Origin allowlist rejects DNS-rebinding and cross-site requests outright.
# Passing a policy to ``create_app`` turns enforcement on; ``None`` (default) leaves
# the app open for tests/embedders. ``serve.run`` always builds one.

# Loopback hostnames the control API always answers to (the default posture).
LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})

# Routes that need no token: the page bootstrap, health, audio clips (loaded by
# <audio>, which cannot send a header), and the Spotify OAuth redirects (top-level
# browser navigations / Spotify's own callback — no header possible; guarded by the
# OAuth ``state`` parameter instead).
_PUBLIC_EXACT = frozenset({"/", "/health", "/spotify/login", "/spotify/callback"})


@dataclass(frozen=True)
class SecurityPolicy:
    """Per-instance control-API policy: a session ``token`` + the ``allowed_hosts``
    (lowercased, port-stripped) the server answers to."""

    token: str
    allowed_hosts: frozenset[str]


def _host_only(value: str) -> str:
    """The hostname from a ``Host``/``netloc`` value, minus any port. Handles IPv6
    literals (``[::1]:80`` → ``::1``)."""
    v = (value or "").strip().lower()
    if v.startswith("["):
        return v[1 : v.index("]")] if "]" in v else v.strip("[]")
    return v.rsplit(":", 1)[0] if ":" in v else v


def new_security_policy(*, host: str, extra_hosts=()) -> SecurityPolicy:
    """A fresh policy: a random session token + the loopback hosts, plus the bind
    host and any explicitly-allowed hosts (a wildcard ``0.0.0.0`` bind is dropped —
    it is not itself a valid ``Host`` value)."""
    hosts = set(LOOPBACK_HOSTS) | {_host_only(host)} | {_host_only(h) for h in extra_hosts}
    hosts.discard("")
    hosts.discard("0.0.0.0")
    return SecurityPolicy(token=_secrets.token_urlsafe(32), allowed_hosts=frozenset(hosts))


def _bearer(auth_header: str) -> str:
    """The token from an ``Authorization: Bearer <token>`` header, or ``""``."""
    h = auth_header or ""
    return h[7:].strip() if h[:7].lower() == "bearer " else ""


def _token_ok(supplied: str, token: str) -> bool:
    try:
        return _secrets.compare_digest(supplied or "", token)
    except TypeError:
        return False


def _ipv4_interfaces() -> list[str]:
    """The host's usable IPv4 addresses for binding: loopback first, then every
    interface's address — LAN, **ppp0**, and **VPN** (tun/wg/tap) included.

    Enumerates all interfaces via ``if_nameindex`` + a ``SIOCGIFADDR`` ioctl
    (Linux), then supplements with the hostname's addresses and the primary
    outbound IP (a UDP probe). Deduplicated, stdlib-only, best-effort (never
    raises); non-Linux hosts fall back to the last two methods."""
    import contextlib
    import socket

    addrs: list[str] = ["127.0.0.1"]

    def _add(ip: str) -> None:
        if ip and ip not in addrs and not ip.startswith("127."):
            addrs.append(ip)

    # 1. Every interface (Linux): catches LAN, ppp0, and VPN tunnels that the
    #    hostname/route probes below miss. fcntl is unix-only; guarded broadly.
    with contextlib.suppress(Exception):
        import fcntl
        import struct

        for _idx, name in socket.if_nameindex():
            with contextlib.suppress(OSError):
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                try:
                    req = struct.pack("256s", name.encode("utf-8")[:15])
                    info = fcntl.ioctl(s.fileno(), 0x8915, req)  # SIOCGIFADDR
                    _add(socket.inet_ntoa(info[20:24]))
                finally:
                    s.close()
    # 2. The hostname's addresses (portable supplement).
    with contextlib.suppress(OSError):
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            _add(info[4][0])
    # 3. The primary outbound IP — a UDP "connect" reveals it without sending
    #    anything (covers hosts whose hostname doesn't resolve to it).
    with contextlib.suppress(OSError):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))
            _add(s.getsockname()[0])
        finally:
            s.close()
    return addrs


def lan_bind_hosts(host: str) -> list[str]:
    """Extra ``Host`` names to accept for a **non-loopback** bind: every one of this
    machine's IPv4 addresses and its hostname — so a client reaches the server via
    whichever address/name it uses (LAN IP, another interface, the hostname) without
    hand-listing them. Empty for a loopback bind (the default). The Host allowlist is
    a DNS-rebinding defense; the session token remains the real gate."""
    if _host_only(host) in {"127.0.0.1", "::1", "localhost"}:
        return []
    import contextlib
    import socket

    hosts = list(_ipv4_interfaces())
    with contextlib.suppress(Exception):
        hosts.append(socket.gethostname())
    return hosts


def _describe_gateway_error(exc: Exception) -> str:
    """A human-readable reason a gateway ``/models`` probe failed, for the UI."""
    import urllib.error

    if isinstance(exc, urllib.error.HTTPError):
        if exc.code in (401, 403):
            return f"auth rejected (HTTP {exc.code}) — check the API key"
        if exc.code == 404:
            return "no /models on this URL (HTTP 404) — check the gateway URL"
        return f"gateway returned HTTP {exc.code}"
    if isinstance(exc, urllib.error.URLError):
        return f"could not reach gateway — {exc.reason}"
    return f"could not read models — {exc}"


def _is_public_path(path: str) -> bool:
    """Routes reachable without the session token."""
    return path in _PUBLIC_EXACT or path.startswith("/audio/")


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
        self.song: dict | None = None  # the current song slot's track (resolved via Spotify)
        self.song_i: int = 0  # song-slot rotation index through the playlist
        # Spotify user OAuth session (Premium in-tab playback + playlists), in-memory.
        self.sp_access_token: str | None = None
        self.sp_refresh_token: str | None = None
        self.sp_expires_at: float = 0.0  # epoch seconds when the access token expires
        self.sp_user: dict | None = None  # {id, name, premium}
        self.sp_oauth_state: str | None = None  # CSRF state for the auth redirect
        self.sp_restored: bool = False  # attempted refresh-token restore this process?
        self.tuning: float = 433.0  # concert-A reference (Hz) for all notes
        self.base_intensity: float = 0.25  # user base energy 0..1 (THETA_START); news lifts it
        self.news_every_s: float | None = None  # news-bulletin cadence (s); None → Director default
        self.refresh_s: float = 1200.0  # source-poll interval (s, 20 min); the serve loop reads this live
        self.broadcasting: bool = True  # when False the refresh loop pauses (no polling/TTS/LLM)
        # Optional LAN binding (Settings tab): off by default → loopback only. When
        # enabled with a chosen host, the *next* start binds there. bound_host is the
        # address this process is actually serving on (set by serve.run).
        self.listen_enabled: bool = False
        self.listen_host: str | None = None
        self.bound_host: str = "127.0.0.1"
        # Drop the Host/Origin allowlist so the app is reachable via any name
        # (tunnel/reverse proxy/public domain). Live; the session token still gates.
        self.allow_any_host: bool = False
        # The Premium section is hidden in the UI unless this is set (config
        # [station] show_premium = true, or $STATEMEDIAFM_SHOW_PREMIUM=1).
        self.show_premium: bool = False
        self.quiet_mode: bool = False  # music only around the news, silent between
        self.music_on: bool = True  # the quiet-mode gate (should the music sound now?)
        self.demo_mode: bool = False  # earlier-milestone feel: HN+git issues every 2 min
        self.demo_topics: list[str] = []  # source topics Demo Mode added (to remove on off)
        self.last_signal = None  # last ActivitySignal, for immediate model/tuning switches
        self.live: bool = False  # LLM writes the news (vs the deterministic offline copy)
        self.news_backend: str = "gateway"  # who writes the news: "claude-cli" | "gateway"
        self.news_model: str | None = None  # LLM model for news parsing (None → offline copy)
        self.news_models: list[str] = []  # gateway models the Settings tab offers
        self.news_cfg = None  # base LLMConfig (for gateway model auto-discovery)
        self.news_temperature: float | None = None  # live [llm] sampling override
        self.news_max_tokens: int | None = None  # live [llm] length override
        self.style: str = "newsroom"  # live-selectable writing style for the news
        self.voice: str = "alan"  # live-selectable narration voice (Piper)
        self.persona: str | None = None  # selected themed persona (None → Custom)
        self.ident: str | None = None  # persona station-ident line (None → default)
        self.signoff: str | None = None  # persona sign-off line (None → default)
        # Live roster: the refresh loop reads these; the Settings tab edits them.
        self.roster: list = []  # (topic, source, cadence, headlines) entries
        self.segments: list[dict] = []  # the segment dicts behind roster (for display)
        self.director = None  # rhythm-of-the-day clock (Director), set by serve.run
        self.session_t0 = None  # monotonic session start, for the "next update in" countdown
        # Persistence hook: serve.run sets this to write the settings file after a
        # UI change (see configstore); None in tests/embedders → nothing persists.
        self.on_change = None
        # "Newscast now" hook: serve.run sets this to air a bulletin immediately from
        # the latest activity, without disturbing the poll/news timers. None → no-op.
        self.air_news_now = None

    def set_plan(self, plan: BroadcastPlan) -> None:
        self.plan = plan
        self.audio = {s.audio.id: s.audio for s in plan.segments if s.audio}

    def set_program(self, program: StrudelProgram) -> None:
        self.program = program


def create_app(state: _State | None = None, *, security: SecurityPolicy | None = None):
    """Build the FastAPI application. Call ``app.state.store.set_plan(...)`` to
    publish a plan for the page and API to serve.

    Pass a :class:`SecurityPolicy` to enforce control-API auth + a Host/Origin
    allowlist (what ``serve.run`` does). ``None`` (default) leaves the app open —
    intended only for tests and in-process embedding on a trusted loopback."""
    from fastapi import Body, FastAPI, HTTPException, Request, Response
    from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

    # `from __future__ import annotations` stringifies the `request: Request`
    # annotation; expose Request in module globals so FastAPI resolves it (else it
    # treats `request` as a missing query param → 422).
    globals()["Request"] = Request

    store = state or _State()
    app = FastAPI(title="State Media FM", version="0.1.0")
    app.state.store = store
    app.state.security = security

    @app.middleware("http")
    async def _persist_after_mutation(request: Request, call_next):
        """After a successful settings mutation, persist the station config (if a
        persistence hook is wired — serve.run sets one; tests/embedders don't)."""
        response = await call_next(request)
        hook = getattr(store, "on_change", None)
        if hook and request.method in ("POST", "DELETE", "PUT") and response.status_code < 400:
            import contextlib

            with contextlib.suppress(Exception):  # persistence must never break a response
                hook()
        return response

    if security is not None:
        from urllib.parse import urlsplit

        @app.middleware("http")
        async def _enforce_security(request: Request, call_next):
            # "Allow any host" (Settings toggle) drops the Host/Origin allowlist so
            # the app is reachable via any name — a tunnel, reverse proxy, or public
            # domain (demos). The session token remains the gate; a page can't read
            # it cross-origin, so the API still can't be forged. Checked live.
            allow_any = bool(getattr(store, "allow_any_host", False))
            # 1. DNS-rebinding defense: only answer to known Host names.
            if not allow_any and _host_only(request.headers.get("host", "")) not in security.allowed_hosts:
                return JSONResponse({"detail": "host not allowed"}, status_code=403)
            # 2. Cross-site defense: a present Origin must be one of our own hosts.
            origin = request.headers.get("origin")
            if origin and not allow_any and _host_only(urlsplit(origin).netloc) not in security.allowed_hosts:
                return JSONResponse({"detail": "cross-origin request blocked"}, status_code=403)
            # 3. Session token on everything but the small public set. The custom
            # header also forces a CORS preflight cross-origin — which the Origin
            # lock (2) denies — so no API request can be forged from another page.
            if not _is_public_path(request.url.path):
                supplied = request.headers.get("x-smfm-token") or _bearer(
                    request.headers.get("authorization", "")
                )
                if not _token_ok(supplied, security.token):
                    return JSONResponse({"detail": "unauthorized"}, status_code=401)
            return await call_next(request)

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    @app.get("/plan")
    def plan() -> dict:
        if store.plan is None:
            return {"segments": []}
        return plan_to_dict(store.plan)


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

    @app.get("/interfaces")
    def interfaces() -> dict:
        """The host's bindable IPv4 addresses, the current LAN-listen selection, and
        the address this process is actually serving on. Powers the Settings toggle
        + dropdown; changing it needs a restart (the bind is fixed at startup)."""
        addrs = _ipv4_interfaces()
        selected = getattr(store, "listen_host", None) or getattr(store, "bound_host", "127.0.0.1")
        if selected not in addrs and selected not in ("127.0.0.1",):
            addrs.append(selected)  # keep a saved-but-currently-absent address visible
        return {
            "addresses": addrs,
            "enabled": bool(getattr(store, "listen_enabled", False)),
            "selected": selected,
            "bound": getattr(store, "bound_host", "127.0.0.1"),
            "allow_any_host": bool(getattr(store, "allow_any_host", False)),
        }

    @app.post("/allow-any-host")
    def set_allow_any_host(on: bool) -> dict:
        """Drop (or restore) the Host/Origin allowlist so the app is reachable via
        any name — a tunnel, reverse proxy, or public domain. Takes effect
        immediately (no restart); the session token still guards the API."""
        store.allow_any_host = bool(on)
        return {"allow_any_host": store.allow_any_host}

    @app.post("/interfaces")
    def set_interfaces(enabled: bool, host: str = "") -> dict:
        """Enable/disable binding to a LAN address, and which one. Persisted; takes
        effect on the next start (loopback stays the default when disabled). A
        non-loopback host must be one the host actually has."""
        addrs = _ipv4_interfaces()
        if enabled:
            if not host or host == "127.0.0.1":
                raise HTTPException(status_code=400, detail="choose a non-loopback address")
            if host not in addrs:
                raise HTTPException(status_code=400, detail="unknown host address")
            store.listen_host = host
        elif host:
            store.listen_host = host  # remember the choice even while disabled
        store.listen_enabled = bool(enabled)
        bound = getattr(store, "bound_host", "127.0.0.1")
        want = store.listen_host if store.listen_enabled else "127.0.0.1"
        return {
            "enabled": store.listen_enabled,
            "selected": store.listen_host or bound,
            "bound": bound,
            "restart_required": (want or "127.0.0.1") != bound,
        }

    @app.post("/news-now")
    def news_now() -> dict:
        """Air a news bulletin **now** from the latest activity, on demand. It does
        not poll sources or reset the source-poll / news-cadence timers — it re-airs
        from what was last gathered. ``aired`` is False if there's no activity yet."""
        fn = getattr(store, "air_news_now", None)
        return {"aired": bool(fn and fn())}

    @app.get("/quiet")
    def quiet() -> dict:
        return {"quiet_mode": store.quiet_mode, "music_on": store.music_on}

    @app.post("/quiet")
    def set_quiet(on: bool) -> dict:
        """Turn quiet mode on/off. On silences the music immediately (quiet mode is
        silent between bulletins — the loop brings it back to lead in before the next
        news); off resumes continuous play."""
        store.quiet_mode = on
        store.music_on = not on
        return {"quiet_mode": store.quiet_mode, "music_on": store.music_on}

    @app.get("/demo")
    def demo() -> dict:
        return {"demo_mode": store.demo_mode}

    @app.post("/demo")
    def set_demo(on: bool) -> dict:
        """Demo Mode: the earlier-milestone feel. Turning it on adds Hacker News
        and a repo's git-issues sources (if not already present) and switches the
        news to a brisk 2-minute cadence (handled in the refresh loop); music
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
            was = store.mix_generators
            store.mix_generators = bool(payload["mix_generators"])
            if was and not store.mix_generators:
                _recompose(store)  # rotation held a mixed generator — restore the selected one
        if "mix_spotify" in payload:
            was = store.mix_spotify
            store.mix_spotify = bool(payload["mix_spotify"])
            if store.mix_spotify and not was:
                from ..serve import publish_song

                publish_song(store)  # surface a song now, don't wait for the slot
            elif not store.mix_spotify:
                store.song = None
        if isinstance(payload.get("selected"), list):
            valid = [m for m in payload["selected"] if m in AMBIENT_MODELS]
            store.mix_models = valid
        return _mix_status()

    @app.get("/song")
    def song() -> dict:
        """The current song slot's track (title/artist + Spotify uri/url/preview),
        or empty when no song is playing."""
        return store.song or {}

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

    @app.get("/next-news")
    def next_news() -> dict:
        """Seconds until the next scheduled news slot (the Director's news cadence
        against the session clock), for the player's 'next update in' countdown.
        ``in_s`` is ``None`` when there's no director/session yet."""
        import math
        import time as _time

        t0 = getattr(store, "session_t0", None)
        director = getattr(store, "director", None)
        if t0 is None or director is None:
            return {"in_s": None, "every_s": None}
        if getattr(store, "demo_mode", False):
            from ..serve import DEMO_NEWS_EVERY_S

            every, offset = float(DEMO_NEWS_EVERY_S), 0.0
        else:
            every, offset = float(director.news.every_s), float(director.news.offset_s)
        elapsed = _time.monotonic() - t0
        nxt = offset + (math.floor((elapsed - offset) / every) + 1) * every
        while nxt <= elapsed:  # guard against a slot landing exactly on now
            nxt += every
        return {"in_s": max(0.0, nxt - elapsed), "every_s": every}

    @app.get("/cadence")
    def cadence() -> dict:
        """The rhythm-of-the-day cadences: how often a news bulletin airs
        (``news_every_s``) and how often sources are polled (``refresh_s``)."""
        return {
            "news_every_s": store.news_every_s,
            "refresh_s": store.refresh_s,
        }

    @app.post("/cadence")
    def set_cadence(news_every: str | None = None, refresh: str | None = None) -> dict:
        """Change the cadences live (no restart). ``news_every``/``refresh`` accept a
        duration (``17m``, ``90s``, ``1h``) or bare seconds; the news cadence
        re-times the running Director immediately."""
        from ..core.schedule import Cadence, parse_duration

        if news_every is not None and str(news_every).strip():
            try:
                secs = parse_duration(news_every)
            except (ValueError, KeyError) as exc:
                raise HTTPException(status_code=400, detail="bad news_every") from exc
            if secs <= 0:
                raise HTTPException(status_code=400, detail="news_every must be > 0")
            store.news_every_s = secs
            if store.director is not None:
                store.director.news = Cadence(secs)  # re-time the live rhythm
        if refresh is not None and str(refresh).strip():
            try:
                secs = parse_duration(refresh)
            except (ValueError, KeyError) as exc:
                raise HTTPException(status_code=400, detail="bad refresh") from exc
            if secs < 1:
                raise HTTPException(status_code=400, detail="refresh must be >= 1s")
            store.refresh_s = float(secs)
        return {"news_every_s": store.news_every_s, "refresh_s": store.refresh_s}

    _NEWS_BACKENDS = ("claude-cli", "gateway")

    def _claude_available() -> bool:
        from ..newsroom.llm import ClaudeCliClient

        return ClaudeCliClient().available()

    @app.get("/news-backend")
    def news_backend() -> dict:
        """Who writes the news: the local Claude Code CLI (the operator's own auth)
        or the LLM gateway. ``claude_available`` hints whether the CLI is installed."""
        return {
            "backend": getattr(store, "news_backend", "claude-cli"),
            "options": list(_NEWS_BACKENDS),
            "claude_available": _claude_available(),
        }

    @app.post("/news-backend")
    def set_news_backend(backend: str) -> dict:
        """Choose the news writer — ``claude-cli`` or ``gateway``. Applies next cycle."""
        if backend not in _NEWS_BACKENDS:
            raise HTTPException(status_code=400, detail="unknown backend")
        store.news_backend = backend
        return {
            "backend": store.news_backend,
            "options": list(_NEWS_BACKENDS),
            "claude_available": _claude_available(),
        }

    @app.get("/gateway-models")
    def gateway_models(probe: bool = True) -> dict:
        """The LLM-gateway models to offer in Settings, plus the current selection.

        With ``probe`` (default) this hits ``GET {gateway}/models`` live — which
        doubles as a **connectivity/auth test** for the gateway: ``ok`` reports
        success, ``error`` a human-readable reason on failure. ``probe=0`` skips
        the network and returns the last-discovered list (for a cheap tab open)."""
        selected = getattr(store, "news_model", None)
        if not probe:
            return {"ok": True, "probed": False, "selected": selected,
                    "models": list(getattr(store, "news_models", []) or [])}
        from ..auth import source_endpoint
        from ..newsroom.llm import LLMConfig, discover_models

        if not source_endpoint("llm-gateway"):
            return {"ok": False, "probed": True, "selected": selected, "models": [],
                    "error": "Set the gateway URL and API key above, then Save."}
        cfg = getattr(store, "news_cfg", None) or LLMConfig(model=selected or "")
        try:
            models = discover_models(cfg, strict=True)
        except (OSError, ValueError) as exc:
            return {"ok": False, "probed": True, "selected": selected, "models": [],
                    "error": _describe_gateway_error(exc)}
        store.news_models = models
        return {"ok": True, "probed": True, "selected": selected, "models": models}

    @app.post("/news-model")
    def set_news_model(model: str = "") -> dict:
        """Choose the gateway model that writes the news (blank → none, no
        bulletins until one is set). Applies next news cycle; persisted."""
        store.news_model = model or None
        return {"model": store.news_model}

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

    @app.get("/sources")
    def sources() -> dict:
        """The live roster (which sources air) and the registered source kinds.
        Tokens are never included — those live in the auth tab."""
        from ..newsroom.tts import voice_names
        from ..roster import source_kinds

        items = [
            {
                "index": i,
                "topic": entry[0],
                "kind": seg.get("source"),
                "every": seg.get("every", "15m"),
                "enabled": seg.get("enabled", True),
                "voice": seg.get("voice") or "random",  # 'random' → the auto rotation
                "config": {k: v for k, v in seg.items() if k != "token"},
            }
            for i, (seg, entry) in enumerate(zip(store.segments, store.roster))
        ]
        return {"sources": items, "kinds": source_kinds(), "voices": voice_names()}

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

    @app.put("/sources/{index}")
    def edit_source(index: int, seg: dict = Body(...)) -> dict:  # noqa: B008 (FastAPI body)
        """Replace the source at ``index`` with a new segment (the Edit-and-save
        flow). Rebuilt like an add, so a bad config is rejected without disturbing
        the running roster."""
        from ..roster import build_segment

        if not 0 <= index < len(store.roster):
            raise HTTPException(status_code=404, detail="no such source")
        if not isinstance(seg, dict) or not seg.get("source"):
            raise HTTPException(status_code=400, detail="a 'source' kind is required")
        try:
            entry = build_segment(seg, index)
        except (ValueError, KeyError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        store.roster[index] = entry
        store.segments[index] = dict(seg)
        return {"index": index, "topic": entry[0]}

    @app.post("/sources/{index}/enabled")
    def set_source_enabled(index: int, on: bool) -> dict:
        """Turn a source on/off without removing it — a disabled source neither
        polls nor airs (e.g. to silence the Hacker News front page), and the flag
        persists. Kept in the segment dict so it survives a restart."""
        if not 0 <= index < len(store.segments):
            raise HTTPException(status_code=404, detail="no such source")
        store.segments[index]["enabled"] = bool(on)
        return {"index": index, "enabled": store.segments[index]["enabled"]}

    @app.post("/sources/{index}/voice")
    def set_source_voice(index: int, voice: str = "") -> dict:
        """Pin the narration voice for this source's news, or clear it. Blank /
        ``random`` → the app's automatic per-source rotation (the default).
        Persisted in the segment so it survives a restart."""
        from ..newsroom.tts import voice_names

        if not 0 <= index < len(store.segments):
            raise HTTPException(status_code=404, detail="no such source")
        v = (voice or "").strip()
        if v and v != "random" and v not in voice_names():
            raise HTTPException(status_code=400, detail="unknown voice")
        if v and v != "random":
            store.segments[index]["voice"] = v
        else:
            store.segments[index].pop("voice", None)  # back to random / auto
        return {"index": index, "voice": store.segments[index].get("voice") or "random"}

    @app.post("/sources/{index}/test")
    def test_source(index: int) -> dict:
        """Poll the source once and report the outcome — ``{ok, count}`` on success,
        or ``{ok: false, detail, status}`` with the HTTP status code when the
        provider returns an error. **Non-consuming**: it ``probe``s (snapshots and
        restores the recency window), so a Test never makes the broadcast miss items."""
        import urllib.error

        if not 0 <= index < len(store.roster):
            raise HTTPException(status_code=404, detail="no such source")
        source = store.roster[index][1]
        try:
            items = source.probe()
            return {"ok": True, "count": len(items)}
        except urllib.error.HTTPError as exc:
            return {"ok": False, "status": exc.code, "detail": f"HTTP {exc.code} {exc.reason}"}
        except urllib.error.URLError as exc:
            return {"ok": False, "status": None, "detail": f"connection failed: {exc.reason}"}
        except Exception as exc:  # noqa: BLE001 — surface any poll error to the operator
            return {"ok": False, "status": None, "detail": str(exc) or type(exc).__name__}

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

    # ── Spotify user OAuth (Premium in-tab playback + playlists) ──────────────
    def _sp_creds() -> tuple[str | None, str | None]:
        from ..auth import source_endpoint, source_token

        return source_endpoint("spotify"), source_token("spotify")

    def _sp_redirect_uri(request: Request) -> str:
        # Spotify's loopback rule requires 127.0.0.1 (not localhost); normalize so
        # the redirect matches the registered URI however the user reached the app.
        base = str(request.base_url).rstrip("/").replace("://localhost", "://127.0.0.1")
        return base + "/spotify/callback"

    def _sp_restore(store) -> None:
        """Restore a Spotify session from the persisted refresh token (survives
        restarts); attempted once per process."""
        import time

        if store.sp_access_token or store.sp_restored:
            return
        store.sp_restored = True
        from ..auth import source_token

        rt = source_token("spotify-user")
        cid, sec = _sp_creds()
        if not (rt and cid and sec):
            return
        try:
            from ..spotify import current_user, refresh_access_token

            d = refresh_access_token(cid, sec, rt)
            store.sp_access_token = d.get("access_token")
            store.sp_refresh_token = d.get("refresh_token") or rt
            store.sp_expires_at = time.time() + int(d.get("expires_in", 3600))
            store.sp_user = current_user(store.sp_access_token)
        except Exception as exc:  # noqa: BLE001 — stay logged out on failure
            print(f"spotify session restore failed: {exc}", file=_sys.stderr)

    def _sp_valid_token(store) -> str | None:
        """A live user access token, refreshing it if it's within 30s of expiry."""
        import time

        _sp_restore(store)
        if not store.sp_access_token:
            return None
        if time.time() < store.sp_expires_at - 30:
            return store.sp_access_token
        cid, sec = _sp_creds()
        if cid and sec and store.sp_refresh_token:
            try:
                from ..spotify import refresh_access_token

                d = refresh_access_token(cid, sec, store.sp_refresh_token)
                store.sp_access_token = d.get("access_token") or store.sp_access_token
                store.sp_expires_at = time.time() + int(d.get("expires_in", 3600))
                if d.get("refresh_token"):
                    store.sp_refresh_token = d["refresh_token"]
            except Exception as exc:  # noqa: BLE001 — keep the old token; the SDK re-asks
                print(f"spotify token refresh failed: {exc}", file=_sys.stderr)
        return store.sp_access_token

    @app.get("/spotify/login")
    def spotify_login(request: Request):
        """Start the Authorization Code flow — redirect the user to Spotify consent."""
        import secrets

        from ..spotify import authorize_url

        cid, sec = _sp_creds()
        if not (cid and sec):
            raise HTTPException(status_code=400, detail="set the Spotify Client ID + Secret first")
        store.sp_oauth_state = secrets.token_urlsafe(16)
        return RedirectResponse(authorize_url(cid, _sp_redirect_uri(request), store.sp_oauth_state))

    @app.get("/spotify/callback")
    def spotify_callback(request: Request, code: str = "", state: str = "", error: str = ""):
        """Spotify redirects back here with a code; swap it for tokens + who's logged in."""
        import time

        from ..spotify import current_user, exchange_code

        if error or not code:
            return RedirectResponse("/?spotify=error")
        if not state or state != store.sp_oauth_state:
            return RedirectResponse("/?spotify=state")
        store.sp_oauth_state = None
        cid, sec = _sp_creds()
        try:
            d = exchange_code(cid, sec, code, _sp_redirect_uri(request))
            store.sp_access_token = d.get("access_token")
            store.sp_refresh_token = d.get("refresh_token")
            store.sp_expires_at = time.time() + int(d.get("expires_in", 3600))
            store.sp_user = current_user(store.sp_access_token)
            if store.sp_refresh_token:  # persist so the session survives restarts
                from ..auth import save_auth_entry

                save_auth_entry("spotify-user", token=store.sp_refresh_token)
        except Exception:  # noqa: BLE001
            return RedirectResponse("/?spotify=error")
        return RedirectResponse("/?spotify=connected")

    @app.get("/spotify/me")
    def spotify_me() -> dict:
        """Connection status + who's logged in (name, whether Premium)."""
        if not _sp_valid_token(store) or not store.sp_user:
            return {"connected": False}
        return {"connected": True, **store.sp_user}

    @app.get("/spotify/token")
    def spotify_web_token() -> dict:
        """A fresh access token for the Web Playback SDK's getOAuthToken callback."""
        import time

        tok = _sp_valid_token(store)
        if not tok:
            raise HTTPException(status_code=401, detail="not connected")
        return {"access_token": tok, "expires_in": max(0, int(store.sp_expires_at - time.time()))}

    @app.get("/spotify/playlists")
    def spotify_playlists() -> dict:
        """The logged-in user's playlists."""
        tok = _sp_valid_token(store)
        if not tok:
            raise HTTPException(status_code=401, detail="not connected")
        from ..spotify import user_playlists

        try:
            return {"playlists": user_playlists(tok)}
        except Exception as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @app.post("/spotify/logout")
    def spotify_logout() -> dict:
        from ..auth import clear_auth_entry

        store.sp_access_token = store.sp_refresh_token = store.sp_user = None
        store.sp_expires_at = 0.0
        store.sp_restored = True  # don't auto-restore after an explicit logout
        clear_auth_entry("spotify-user")  # forget the persisted refresh token too
        return {"connected": False}

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
            _render_page(store, security.token if security else None),
            headers={"Cache-Control": "no-store, must-revalidate"},
        )

    return app


def _render_page(store: _State, token: str | None = None) -> str:
    """The Tufte player page. Static: the browser polls /genmusic and /plan and
    plays the generative music with Strudel, crossfading as programs change. When
    the instance is secured, the per-session ``token`` is embedded so same-origin
    API calls carry it automatically (see the bootstrap script in the page)."""
    return (
        _PLAYER_HTML
        .replace("__SMFM_TOKEN_JSON__", _json.dumps(token or ""))
        .replace("__SMFM_SHOW_PREMIUM__", "true" if getattr(store, "show_premium", False) else "false")
    )


# Loaded once. The page fetches /plan (news) and /genmusic (Strudel program text)
# on an interval; a start button satisfies the browser's audio-gesture rule, then
# each changed program is evaluate()'d (its built-in .fadeIn crossfades the swap).
# An incidental canvas visualizer reflects intensity + brainwave band.
_PLAYER_HTML = r"""<!doctype html><meta charset='utf-8'>
<meta name='viewport' content='width=device-width, initial-scale=1'>
<title>State Media FM</title>
<script>/* Control-API auth: attach the per-session token to same-origin (/…) API
   calls. A cross-origin page can't read this page's body, so it can't learn the
   token; absolute-URL fetches (Strudel CDN, samples) are left untouched. */
(function(){var T=__SMFM_TOKEN_JSON__;if(!T)return;var of=window.fetch.bind(window);
window.fetch=function(u,o){o=o||{};var url=(typeof u==='string')?u:(u&&u.url)||'';
if(url&&url.charAt(0)==='/'){var h=new Headers(o.headers||{});h.set('X-SMFM-Token',T);o.headers=h;}
return of(u,o);};})();</script>
<style>
  body{max-width:44rem;margin:6vh auto;padding:0 1.25rem;
       font:16px/1.55 Georgia,'Times New Roman',serif;color:#111;background:#fffff8}
  h1{font-weight:normal;letter-spacing:.02em;margin:0}
  h2{font-weight:normal;font-size:1.05rem;margin:.2rem 0}
  .muted{color:#666;font-size:.85rem;font-style:italic}
  button{font:inherit;padding:.5rem 1rem;margin:1rem 0;cursor:pointer;
         background:#111;color:#fffff8;border:0;border-radius:2px}
  button[disabled]{opacity:.6;cursor:default}
  /* Transport buttons (Play/Pause/Stop/Newscast): uniform, rounded "pill" outline. */
  button.icon{min-width:2.8rem;font-size:1.05rem;line-height:1;text-align:center;
              padding:.4rem 1.1rem;margin:0;background:transparent;color:inherit;
              border:1px solid #bbb;border-radius:999px}
  button.icon:hover:not([disabled]){border-color:#888}
  /* Lit (amber) while engaged — e.g. Pause when the radio is paused, press to resume. */
  button.icon.active{background:#e8a13a;color:#151515;border-color:#e8a13a;box-shadow:none}
  #modelwrap,#tuningwrap,#quietwrap,#intensitywrap{display:inline-block;margin-left:1rem}
  #intensity{vertical-align:middle;width:6rem}
  select{font:inherit;font-size:.85rem;font-weight:normal;margin-left:.35rem}
  #tabs{margin:.3rem 0 1rem;border-bottom:1px solid #ccc}
  #tabs a{cursor:pointer;display:inline-block;padding:.3rem .7rem;margin-right:.2rem;
          color:#666;border-bottom:2px solid transparent}
  #tabs a.active{color:#111;border-bottom-color:#111}
  .authrow{margin:.4rem 0;padding:.6rem 0;border-top:1px solid #eee}
  /* Premium feature list — each item prefixed with a padlock (locked). */
  .locked-list{list-style:none;margin:.4rem 0;padding-left:.2rem}
  .locked-list li{margin:.3rem 0}
  .locked-list li::before{content:'🔒';margin-right:.5rem;opacity:.75}
  /* "?" token-path helper (native tooltip on hover). */
  .help{display:inline-block;width:1.15em;height:1.15em;line-height:1.15em;text-align:center;
        border:1px solid #999;border-radius:50%;font-size:.72rem;color:#666;cursor:help;
        user-select:none;vertical-align:middle}
  @media(prefers-color-scheme:dark){.help{border-color:#666;color:#aaa}}
  .authrow input{font:inherit;font-size:.9rem;display:block;width:100%;max-width:26rem;margin:.2rem 0;
                 padding:.3rem;border:1px solid #ccc;border-radius:2px;background:#fffff8;color:inherit}
  /* Warning line + revealable info hint (least-privilege scopes). */
  .warn{font-size:.85rem;color:#7a5200;background:#fff8e6;border:1px solid #e8d59a;
        border-radius:4px;padding:.4rem .6rem;margin:.4rem 0}
  details.hint{margin:.3rem 0 .5rem}
  details.hint>summary{cursor:pointer;font-size:.85rem;color:#555;user-select:none}
  details.hint ul{margin:.3rem 0;padding-left:1.2rem;font-size:.85rem;line-height:1.5}
  @media(prefers-color-scheme:dark){.warn{color:#f0d38a;background:#2a2410;border-color:#5c4f1f}
    details.hint>summary{color:#aaa}}
  /* Checkboxes sit inline with their label, not as full-width block inputs. */
  .authrow input[type=checkbox]{display:inline-block;width:auto;max-width:none;margin:0 .35rem 0 0;
                 padding:0;border:0;vertical-align:middle}
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
  .srcrow{display:flex;flex-wrap:wrap;align-items:baseline;gap:.5rem;margin:.3rem 0;
          padding:.35rem 0;border-top:1px solid #eee}
  .srcrow .grow{flex:1;min-width:10rem} .srcrow .kind{font-variant:small-caps;color:#666}
  .srcrow .src-result{font-style:italic}
  /* Compact voice picker: initial-only when closed; capped width so it can't widen
     or wrap the row even while focused (the open menu still shows full names). */
  .srcrow .src-voice{font-size:.75rem;margin:0;padding:.1rem .1rem;width:auto;
                     min-width:2.1rem;max-width:2.8rem;flex:0 0 auto;text-align:center}
  .srcrow.off{opacity:.55} .srcrow.off .grow{text-decoration:line-through}
  /* compact toggle for the source rows */
  .srcrow .switch .track{width:2rem;height:1.1rem}
  .srcrow .switch .track::after{width:.85rem;height:.85rem}
  .srcrow .switch input:checked + .track::after{transform:translateX(.9rem)}
  .srcrow button{margin:0;padding:.25rem .6rem;font-size:.8rem;background:transparent;
                 color:inherit;border:1px solid #bbb;border-radius:2px}
  #newsbadge{margin-left:1rem}
  @media(prefers-color-scheme:dark){
    #tabs{border-color:#333} #tabs a.active{color:#eee;border-bottom-color:#eee}
    .authrow{border-color:#333} .authrow input{background:#111;color:#eee;border-color:#444}
    .srcrow{border-color:#333} .chip,.srcrow button{border-color:#555}
    .switch .track{background:#444}}
  /* Player modes: Flow State (generative) vs Playlist (Spotify) — a segmented pick. */
  /* Mode selector (Flow State / Playlist): tabs, like the Player/Settings tabs. */
  #modes{display:flex;gap:.2rem;margin:.7rem 0;border-bottom:1px solid #ccc}
  #modes button{font:inherit;font-size:.95rem;padding:.3rem .7rem;margin:0;cursor:pointer;
    background:transparent;color:#666;border:0;border-bottom:2px solid transparent;border-radius:0}
  #modes button.active{color:#111;border-bottom-color:#111}
  @media(prefers-color-scheme:dark){#modes{border-color:#333}
    #modes button.active{color:#eee;border-bottom-color:#eee}}
  /* Player transport + control bars: coherent rows, not a loose list. */
  #transport,.bar{display:flex;flex-wrap:wrap;align-items:center;gap:.7rem;margin:.5rem 0}
  #transport{padding:.6rem .1rem;border-top:1px solid #ccc;border-bottom:1px solid #ccc}
  .bar{font-size:.9rem}
  .grow{flex:1;min-width:8rem}
  @media(prefers-color-scheme:dark){#transport{border-color:#333}
    button.icon{border-color:#555}button.icon:hover:not([disabled]){border-color:#999}}
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
  /* Typography: prose (body/p/li/article text) stays serif; every heading, label,
     and control is sans-serif. Listed last so it wins the cascade — and includes
     the higher-specificity control rules (.authrow input) it must override. */
  h1,h2,h3,h4,summary,label,button,select,textarea,input,.authrow input,
  .chip,#tabs a,#modes button{
    font-family:system-ui,-apple-system,'Segoe UI',Roboto,Helvetica,Arial,sans-serif}
  /* Themes (Settings › Theme) apply via <html data-theme="…">. The default look is
     the base styling above; reverting to Adequate simply drops these overrides. */

  /* ── Analog: dark slate faceplate, white Letraset lettering, early-synth panels ── */
  html[data-theme='analog']{
    --an-desk:#1b2125; --an-face1:#333c44; --an-face2:#283037;
    --an-panel1:#39424a; --an-panel2:#2c343b; --an-groove:#0f1417;
    --an-ink:#f4f4ef; --an-dim:#98a4ad; --an-led:#e8a13a;
    background:var(--an-desk);
  }
  /* The whole column is a device faceplate sitting on a dark desk. */
  html[data-theme='analog'] body{
    background:linear-gradient(180deg,var(--an-face1),var(--an-face2));
    color:var(--an-ink); border:1px solid var(--an-groove); border-radius:8px;
    padding:1rem 1.4rem 1.6rem;
    box-shadow:0 10px 34px rgba(0,0,0,.55), inset 0 1px 0 rgba(255,255,255,.06);
    display:flex; flex-direction:column;  /* lets the title drop to the footer (order) */
  }
  /* Letraset lettering: white, uppercase, letter-spaced, lifted off the panel. */
  html[data-theme='analog'] h1,html[data-theme='analog'] h2,html[data-theme='analog'] h3,
  html[data-theme='analog'] summary,html[data-theme='analog'] label,
  html[data-theme='analog'] #tabs a{
    color:var(--an-ink); text-transform:uppercase; letter-spacing:.12em;
    font-family:'Helvetica Neue',Helvetica,Arial,sans-serif; text-shadow:0 1px 0 rgba(0,0,0,.6);
  }
  /* Title + headings: a narrower condensed face. */
  html[data-theme='analog'] h1,html[data-theme='analog'] h2,html[data-theme='analog'] h3,
  html[data-theme='analog'] summary{
    font-family:'Arial Narrow','Helvetica Neue Condensed','Roboto Condensed','Helvetica Neue',Arial,sans-serif;
    font-stretch:condensed;
  }
  /* Title moves to the footer: last in the flex order, set off by a groove line. */
  html[data-theme='analog'] h1{ letter-spacing:.02em; font-weight:normal; text-align:center;
    order:99; margin-top:1.6rem; padding-top:1rem; border-top:1px solid var(--an-groove) }
  html[data-theme='analog'] .muted{ color:var(--an-dim) }
  /* Energy + News-voice labels/states read upright, not italic. */
  html[data-theme='analog'] #intensitywrap,html[data-theme='analog'] #voicewrap,
  html[data-theme='analog'] #intensity-band{ font-style:normal }
  html[data-theme='analog'] a{ color:var(--an-ink) }
  /* Panels: bevelled synth modules — inset highlight on top, shadow below. */
  html[data-theme='analog'] details.section,html[data-theme='analog'] article{
    background:linear-gradient(180deg,var(--an-panel1),var(--an-panel2));
    border:1px solid var(--an-groove); border-radius:5px; margin:.6rem 0; padding:.5rem .85rem;
    box-shadow:inset 0 1px 0 rgba(255,255,255,.07), 0 2px 4px rgba(0,0,0,.45);
  }
  html[data-theme='analog'] details.section>summary::before{ color:var(--an-led) }
  html[data-theme='analog'] .authrow{ border-top:1px solid rgba(255,255,255,.06) }
  html[data-theme='analog'] .bar{ border-color:rgba(255,255,255,.08) }
  /* Buttons: raised metal caps; press inset. */
  html[data-theme='analog'] button{
    background:linear-gradient(180deg,#4b555d,#39424a); color:var(--an-ink);
    border:1px solid var(--an-groove); border-radius:4px;
    text-transform:uppercase; letter-spacing:.08em; font-weight:600;
    box-shadow:inset 0 1px 0 rgba(255,255,255,.14), 0 2px 3px rgba(0,0,0,.5);
  }
  html[data-theme='analog'] button:hover{ background:linear-gradient(180deg,#545f68,#3f4952) }
  html[data-theme='analog'] button:active{ box-shadow:inset 0 2px 5px rgba(0,0,0,.6); transform:translateY(1px) }
  html[data-theme='analog'] button[disabled]{ opacity:.45 }
  /* Mode selector renders as tabs (LED underline), not metal buttons. */
  html[data-theme='analog'] #modes{ border-bottom:1px solid var(--an-groove) }
  html[data-theme='analog'] #modes button{ background:none; border:0; border-bottom:2px solid transparent;
    border-radius:0; box-shadow:none; color:var(--an-dim); text-transform:uppercase; letter-spacing:.1em }
  html[data-theme='analog'] #modes button:hover{ background:none }
  html[data-theme='analog'] #modes button.active{ color:var(--an-ink); border-bottom-color:var(--an-led) }
  html[data-theme='analog'] .chip{ background:linear-gradient(180deg,#414b53,#333c43); color:var(--an-ink);
    border:1px solid var(--an-groove) }
  /* Inset fields: recessed into the panel. */
  html[data-theme='analog'] input,html[data-theme='analog'] select,
  html[data-theme='analog'] textarea,html[data-theme='analog'] .authrow input{
    background:#1a1f23; color:var(--an-ink); border:1px solid var(--an-groove); border-radius:3px;
    box-shadow:inset 0 1px 3px rgba(0,0,0,.55);
  }
  /* Tabs: a labelled front panel; the active tab is lit by an LED underline. */
  html[data-theme='analog'] #tabs{ border-bottom-color:var(--an-groove) }
  html[data-theme='analog'] #tabs a{ color:var(--an-dim) }
  html[data-theme='analog'] #tabs a.active{ color:var(--an-ink); border-bottom-color:var(--an-led) }
  html[data-theme='analog'] .newslist a{ color:var(--an-ink); border-bottom-color:#5a6570 }
  html[data-theme='analog'] .warn{ color:#f0d38a; background:#2a2410; border-color:#5c4f1f }

  /* ── Vapor: vaporwave — neon sunset + grid, magenta/cyan glow, translucent panels ── */
  html[data-theme='vapor']{
    --vw-pink:#ff71ce; --vw-cyan:#01cdfe; --vw-purple:#b967ff; --vw-mint:#05ffa1; --vw-yellow:#fffb96;
    --vw-ink:#f4ecff; --vw-dim:#c9b3ff; --vw-panel:rgba(38,18,74,.62); --vw-edge:rgba(1,205,254,.55);
    /* Three drifting colour blobs (aurora) + a neon grid over a purple→magenta
       sunset. Only the blobs move; the grid and sunset stay put. */
    background:
      radial-gradient(55% 50% at 50% 50%, rgba(5,255,161,.20), transparent 70%),
      radial-gradient(60% 55% at 50% 50%, rgba(1,205,254,.20), transparent 70%),
      radial-gradient(65% 55% at 50% 50%, rgba(255,113,206,.18), transparent 70%),
      repeating-linear-gradient(transparent 0 38px, rgba(1,205,254,.07) 38px 40px),
      repeating-linear-gradient(90deg, transparent 0 38px, rgba(255,113,206,.06) 38px 40px),
      linear-gradient(180deg,#160a2e,#3a1f6e 45%,#8a2d8f 72%,#e15aa5 100%);
    background-size:140% 140%,150% 150%,160% 160%,auto,auto,cover;
    background-position:18% 28%,82% 38%,48% 78%,0 0,0 0,center;
    background-repeat:no-repeat,no-repeat,no-repeat,repeat,repeat,no-repeat;
    background-attachment:fixed;
    /* ~44s, so the drift is near-imperceptible — but plain if you watch a while. */
    animation:vaporAurora 44s ease-in-out infinite alternate;
  }
  @keyframes vaporAurora{
    0%  { background-position:18% 28%,82% 38%,48% 78%,0 0,0 0,center }
    50% { background-position:31% 41%,67% 53%,60% 65%,0 0,0 0,center }
    100%{ background-position:23% 33%,77% 45%,52% 73%,0 0,0 0,center }
  }
  /* Honour reduced-motion: hold the aurora still. */
  @media (prefers-reduced-motion:reduce){ html[data-theme='vapor']{ animation:none } }
  html[data-theme='vapor'] body{
    background:var(--vw-panel); color:var(--vw-ink);
    border:1px solid var(--vw-edge); border-radius:10px; padding:1rem 1.4rem 1.6rem;
    box-shadow:0 0 22px rgba(1,205,254,.35), 0 0 44px rgba(255,113,206,.22), inset 0 0 30px rgba(185,103,255,.12);
    backdrop-filter:blur(2px);
  }
  /* Neon lettering. */
  html[data-theme='vapor'] h1,html[data-theme='vapor'] h2,html[data-theme='vapor'] h3,
  html[data-theme='vapor'] summary{
    color:#fff; text-transform:uppercase; letter-spacing:.14em;
    text-shadow:0 0 6px var(--vw-pink),0 0 14px var(--vw-purple),0 0 22px var(--vw-cyan);
  }
  html[data-theme='vapor'] h1{ text-align:center; letter-spacing:.3em; font-weight:600 }
  html[data-theme='vapor'] label{ color:var(--vw-cyan); letter-spacing:.05em }
  html[data-theme='vapor'] .muted{ color:var(--vw-dim) }
  html[data-theme='vapor'] a{ color:var(--vw-cyan) }
  /* Translucent neon panels. */
  html[data-theme='vapor'] details.section,html[data-theme='vapor'] article{
    background:linear-gradient(180deg,rgba(58,31,110,.55),rgba(138,45,143,.35));
    border:1px solid var(--vw-edge); border-radius:8px; margin:.6rem 0; padding:.5rem .85rem;
    box-shadow:0 0 12px rgba(1,205,254,.25), inset 0 0 18px rgba(255,113,206,.10);
  }
  html[data-theme='vapor'] details.section>summary::before{ color:var(--vw-mint) }
  html[data-theme='vapor'] .authrow{ border-top:1px solid rgba(1,205,254,.25) }
  html[data-theme='vapor'] .bar{ border-color:rgba(1,205,254,.3) }
  /* Neon buttons. */
  html[data-theme='vapor'] button{
    background:linear-gradient(90deg,var(--vw-pink),var(--vw-cyan)); color:#1b0b2e;
    border:0; border-radius:6px; text-transform:uppercase; letter-spacing:.08em; font-weight:700;
    box-shadow:0 0 10px rgba(255,113,206,.5),0 0 18px rgba(1,205,254,.35);
  }
  html[data-theme='vapor'] button:hover{ filter:brightness(1.12) }
  html[data-theme='vapor'] button:active{ transform:translateY(1px); filter:brightness(.95) }
  html[data-theme='vapor'] button[disabled]{ opacity:.5; filter:grayscale(.3) }
  /* Mode selector renders as neon-lit tabs, not filled buttons. */
  html[data-theme='vapor'] #modes{ border-bottom:1px solid rgba(1,205,254,.4) }
  html[data-theme='vapor'] #modes button{ background:none; border:0; border-bottom:2px solid transparent;
    border-radius:0; box-shadow:none; color:var(--vw-dim); font-weight:600;
    text-transform:uppercase; letter-spacing:.08em }
  html[data-theme='vapor'] #modes button:hover{ filter:none; color:#fff }
  html[data-theme='vapor'] #modes button.active{ color:#fff; border-bottom-color:var(--vw-pink);
    text-shadow:0 0 8px var(--vw-pink) }
  html[data-theme='vapor'] .chip{ background:linear-gradient(90deg,rgba(255,113,206,.9),rgba(1,205,254,.9));
    color:#1b0b2e; border:0 }
  /* Glowing inset fields. */
  html[data-theme='vapor'] input,html[data-theme='vapor'] select,
  html[data-theme='vapor'] textarea,html[data-theme='vapor'] .authrow input{
    background:rgba(20,10,42,.7); color:var(--vw-ink); border:1px solid var(--vw-edge); border-radius:5px;
  }
  html[data-theme='vapor'] input:focus,html[data-theme='vapor'] select:focus,
  html[data-theme='vapor'] .authrow input:focus{ outline:0; border-color:var(--vw-pink);
    box-shadow:0 0 10px rgba(255,113,206,.6) }
  /* Tabs lit in neon. */
  html[data-theme='vapor'] #tabs{ border-bottom-color:rgba(1,205,254,.4) }
  html[data-theme='vapor'] #tabs a{ color:var(--vw-dim); text-transform:uppercase; letter-spacing:.08em }
  html[data-theme='vapor'] #tabs a.active{ color:#fff; border-bottom-color:var(--vw-pink);
    text-shadow:0 0 8px var(--vw-pink) }
  html[data-theme='vapor'] .newslist a{ color:var(--vw-cyan); border-bottom-color:rgba(1,205,254,.5) }
  html[data-theme='vapor'] .warn{ color:#1b0b2e; background:var(--vw-yellow); border-color:#e0d060 }
</style>
<h1>State Media FM</h1>
<nav id='tabs'><a data-tab='player' class='active'>Player</a><a data-tab='settings'>Settings</a></nav>
<div id='player-view'>
  <!-- Primary transport, shared across both modes: play the radio, stop it, air a
       bulletin on demand, and toggle quiet mode. -->
  <div id='transport'>
    <button id='play' class='icon' aria-label='Play' title='Play'>▶</button>
    <button id='pause' class='icon' aria-label='Pause' title='Pause'>▮▮</button>
    <button id='stop' class='icon' aria-label='Stop' title='Stop'>■</button>
    <button id='news-now' class='icon' aria-label='Newscast now' title='Newscast now — air a bulletin from the latest activity (does not reset the source timer)'>▶▶</button>
    <label class='muted' id='quietwrap'><input type='checkbox' id='quiet'> quiet mode</label>
    <span class='muted grow' id='status'>press Play</span>
    <span class='muted' id='next-update'></span>
  </div>

  <!-- The bed the news plays over: generative Flow State, or your Spotify. -->
  <div id='modes'>
    <button data-mode='flow' class='active'>Flow State</button>
    <button data-mode='playlist'>Playlist</button>
  </div>

  <!-- FLOW STATE: the generative music's live controls -->
  <div id='flow-panel'>
    <div class='bar'>
      <label class='muted' id='intensitywrap'>energy
        <input type='range' id='intensity' min='0' max='1' step='0.05'>
        <span id='intensity-band'></span></label>
    </div>
  </div>

  <!-- PLAYLIST: your Spotify (Premium). Play/Stop are the shared transport above. -->
  <div id='playlist-panel' hidden>
    <div id='spotify-bar' class='bar' hidden>
      <button id='sp-connect'>Connect Spotify (Premium)</button>
      <span class='muted' id='sp-who'></span>
      <select id='sp-playlist' hidden></select>
      <button id='sp-skip' hidden>Skip</button>
      <button id='sp-logout' hidden>Disconnect</button>
      <label class='muted' id='spvolwrap' hidden title='Background playlist volume, relative to the news voice'>music
        <input type='range' id='spvol' min='0' max='1' step='0.05'></label>
      <span class='muted grow' id='sp-msg'></span>
    </div>
  </div>

  <!-- Shared output: news-voice level, visualizer, now-playing, bulletins -->
  <div class='bar'>
    <label class='muted' id='voicewrap'>news voice
      <input type='range' id='voicevol' min='0' max='1' step='0.05'></label>
  </div>
  <canvas id='viz'></canvas>
  <section id='song'></section>
  <section id='news'><p class='muted'>Loading…</p></section>
</div>
<div id='settings-view' hidden>
  <!-- Demo Mode: always at the very top -->
  <div class='authrow' id='demo-row'>
    <label class='switch'><input type='checkbox' id='demo'><span class='track'></span>
      <strong>Demo Mode</strong></label>
    <span class='muted' id='demo-status'></span>
  </div>
  <p class='muted'>Reads the Hacker News front page and a repo's git issues every 2
  minutes, music in between. Turning it on adds those two sources; off removes them.</p>

  <details class='section' open>
    <summary>News Update Sources</summary>
    <p class='muted'>Which activity State Media FM airs. Changes apply to the running
    session (not written to the config file). Each source authenticates with a token
    you set under <em>Auth</em> — grant it a <strong>read-only, least-privilege scope</strong>
    (see the recommended scopes there).</p>
    <div id='sourcelist'></div>
    <div class='authrow'>
      <select id='src-kind'></select>
      <input id='src-topic' placeholder='topic (optional)'>
      <input id='src-param' placeholder='—'>
      <input id='src-maxage' placeholder='max age since issue opened (default 60d)' hidden>
      <input id='src-every' placeholder='every (e.g. 15m)' value='15m'>
      <input id='src-headlines' type='number' min='1' placeholder='headlines (max read)'>
      <input id='src-maxcount' type='number' min='1' placeholder='max_count (items polled)'>
      <input id='src-offset' placeholder='offset (e.g. 0, 5m)'>
      <button id='src-add'>Add source</button>
      <button id='src-cancel' hidden>Cancel</button>
      <span class='muted' id='src-status'></span>
    </div>
  </details>

  <details class='section'>
    <summary>Cadence</summary>
    <p class='muted'>The rhythm of the day: how often a news bulletin airs, and how
    often sources are polled. Accepts a duration (<code>17m</code>, <code>90s</code>,
    <code>1h</code>) or bare seconds. Applies immediately.</p>
    <div class='authrow'>
      <label class='muted'>news every <input id='cad-news' placeholder='17m'></label>
      <label class='muted'>refresh <input id='cad-refresh' placeholder='60s'></label>
      <button id='cad-save'>Apply</button>
      <span class='muted' id='cad-status'></span>
    </div>
  </details>

  <details class='section'>
    <summary>Mix</summary>
    <div class='authrow' hidden>
      <label class='muted' id='modelwrap'>ambient generator
        <select id='model'></select>
      </label>
      <label class='muted' id='tuningwrap'>tuning A=
        <select id='tuning'></select>
      </label>
    </div>
    <p class='muted'>The ambient bed is the generative composition
    <em>Radiator</em> by James Reid.</p>
    <div class='authrow' id='voice-row' hidden>
      <label class='muted'>voice <select id='voice-sel'></select></label>
      <button id='narration-save'>Apply</button>
      <span class='muted' id='narration-status'></span>
    </div>
    <h3>Spotify</h3>
    <p class='muted'>Connect Spotify to resolve song slots to tracks. Create an app at
    <code>developer.spotify.com</code> and paste its Client ID + Client Secret —
    stored locally in the gitignored auth file, the secret masked and never sent
    anywhere but your own server.</p>
    <p class='warn'>⚠ Playing Spotify playlists needs an <strong>open, logged-in
    Spotify web-player session</strong> in this browser (Premium) — playback runs
    through Spotify's Web Playback SDK in this tab, so if you're signed out of
    Spotify it won't work.</p>
    <div class='authrow'>
      <input id='sp-id' placeholder='Client ID'>
      <input id='sp-secret' type='password' autocomplete='off' placeholder='Client Secret'>
      <button id='sp-save'>Save</button>
      <button id='sp-test'>Test connection</button>
      <span class='muted' id='sp-status'></span>
    </div>
  </details>

  <details class='section'>
    <summary>Auth</summary>
    <p class='muted'>Endpoints and tokens for the services State Media FM connects to —
    the activity sources it polls (GitHub, GitLab, Jira, Slack, PagerDuty) and the LLM
    gateway that writes the news. Stored locally in a gitignored file
    (<code>statemediafm.auth.toml</code>, owner-only); tokens are masked here and never
    committed or sent anywhere but your own server.</p>
    <p class='warn'>⚠ State Media FM only <strong>reads</strong> activity. Grant each token
    the <strong>narrowest, read-only scope</strong> the provider allows — never write or
    admin. A leaked token can do only what you scoped it for.</p>
    <details class='hint'>
      <summary>ⓘ Recommended token scopes (least privilege)</summary>
      <ul class='muted'>
        <li><strong>GitHub</strong> — a fine-grained PAT limited to the repos you add, with
          <em>read-only</em> Contents + Issues + Pull&nbsp;requests (classic: <code>public_repo</code>
          for public repos, or <code>repo</code> read-only). No write, no admin, no org scopes.</li>
        <li><strong>GitLab</strong> — a personal/project token with only <code>read_api</code>
          (or <code>read_repository</code>). Nothing write. The <em>to-dos</em> source
          (<code>/dashboard/todos</code>) is user-scoped, so it needs that account's own
          <code>read_api</code> PAT.</li>
        <li><strong>Jira</strong> — an API token on a <em>least-privileged account</em> that can only
          <em>Browse projects</em> (read). The token inherits the account's permissions, so scope the
          account, not just the token.</li>
        <li><strong>Slack</strong> — a bot/user token with <code>channels:read</code> +
          <code>channels:history</code> (read-only) for the channels you add; no write/post scopes.</li>
        <li><strong>PagerDuty</strong> — a <em>read-only</em> REST API key.</li>
        <li><strong>llm-gateway</strong> — an API key scoped to only the model(s) you use, and (if the
          gateway supports it) a spend cap.</li>
      </ul>
      <p class='muted'>If a token leaks, revoke it at the provider — this app can't. Storage and the
      full trust model are in <code>SECURITY_MODEL.md</code>.</p>
    </details>
    <p class='muted'><strong>Self-hosted GitLab / GitHub Enterprise:</strong> set the GitLab or
    GitHub <em>endpoint</em> to your instance URL (e.g. <code>https://gitlab.mycorp.com</code> or
    <code>https://ghe.mycorp.com</code>) so projects on it are recognized and polled via its API
    (GHE uses <code>/api/v3</code>) — leave it blank for <code>gitlab.com</code> / <code>github.com</code>.
    Set the endpoint/token <strong>before</strong> adding a project under <em>News Update Sources</em>
    (a value saved after a source is added only applies once you re-add it).</p>
    <div id='authform'></div>
    <h3>News writer</h3>
    <p class='muted'>Who writes the news bulletins — your local <strong>Claude&nbsp;CLI</strong>
    (uses the Claude&nbsp;Code login you already have, no API key) or an
    <strong>LLM&nbsp;gateway</strong>. Applies next news cycle.</p>
    <div class='authrow'>
      <label class='muted'>backend
        <select id='news-backend'>
          <option value='claude-cli'>Local Claude CLI</option>
          <option value='gateway'>LLM gateway</option>
        </select></label>
      <span class='muted' id='news-backend-status'></span>
    </div>
    <h3>LLM gateway</h3>
    <p class='muted'>Used only when the writer above is <em>LLM gateway</em>. A gateway
    (LiteLLM, OpenRouter, Azure, a self-hosted vLLM/Ollama/NIM, …): its base
    <strong>URL</strong> and <strong>API key</strong>; the model comes from your run
    config (<code>[llm]</code> / <code>model_config.yaml</code>).</p>
    <div id='gatewayform'></div>
    <p class='muted'>Once the URL + key are saved, load the model list from the gateway
    itself and pick which one writes the news. Loading the list <strong>tests the
    gateway</strong> — success means it answered; an error shows why.</p>
    <div class='authrow' id='gw-model-row'>
      <label class='muted'>news model <select id='gw-model'><option value=''>— none —</option></select></label>
      <button id='gw-load'>Test gateway &amp; load models</button>
      <span class='muted' id='gw-model-status'></span>
    </div>
    <p class='muted'>Quick-fill from a provider preset (sets the URL slot above and suggests a
    news model — you still enter the API key in the token slot):</p>
    <div id='presets'></div>
    <h3>Local network access</h3>
    <p class='muted'>By default the app is reachable only from this machine
    (<code>127.0.0.1</code>). Turn this on to also serve on a LAN address so other
    devices on your network can open it. <strong>Applies on the next start.</strong></p>
    <p class='warn'>⚠ Binding a LAN address makes the control API network-reachable
    (it stays session-token-protected and host-locked). Only enable this on a
    trusted network.</p>
    <div class='authrow'>
      <label class='switch'><input type='checkbox' id='lan-enabled'><span class='track'></span>
        <strong>Listen on a local network address</strong></label>
      <label class='muted'>address <select id='lan-host'></select></label>
      <span class='muted' id='lan-status'></span>
    </div>
    <div class='authrow'>
      <label class='switch'><input type='checkbox' id='lan-anyhost'><span class='track'></span>
        <strong>Allow any host</strong></label>
      <span class='muted' id='lan-anyhost-status'>reach it via a tunnel, reverse proxy, or public domain (applies immediately)</span>
    </div>
  </details>

  <details class='section'>
    <summary>Theme</summary>
    <div class='authrow'>
      <select id='theme-sel'>
        <option value='adequate'>Adequate</option>
        <option value='analog'>Analog</option>
        <option value='vapor'>Vapor</option>
      </select>
    </div>
  </details>

  <details class='section' id='sec-premium' hidden>
    <summary>Premium</summary>
    <p class='muted'>Future features TBD:</p>
    <ul class='locked-list'>
      <li>Enterprise streaming-service mode</li>
      <li>SSO</li>
      <li>Commuter mobile app</li>
      <li>Team playlists</li>
      <li>Personas</li>
    </ul>
    <div class='authrow' id='license-row'>
      <input id='license-key' type='password' autocomplete='off' placeholder='premium key'>
      <button id='license-save'>Activate</button>
      <span class='muted' id='license-status'></span>
    </div>
  </details>

  <details class='section'>
    <summary>Contact</summary>
    <p>Open an issue at <a href='https://gitlab.com/statemediafm' target='_blank' rel='noopener'>https://gitlab.com/statemediafm</a>
    or JAMIE dot F dot REID at GMAIL dot COM.</p>
  </details>
</div>

<script src='https://unpkg.com/@strudel/web@1.0.3'></script>
<script>
const statusEl=document.getElementById('status');
const newsEl=document.getElementById('news');
const btn=document.getElementById('play');
const modelSel=document.getElementById('model');

// Shared transport: Play starts/resumes the radio in the current mode (Flow State
// generative bed, or your Spotify playlist); Stop halts it. Newscast-now airs a
// bulletin on demand. Play requires a user gesture (browsers block audio until one).
let broadcasting=true;
let paused=false;        // Pause halts audio but keeps the session so Play resumes
let strudelReady=false;  // the generative engine is initialised + warmed
function updateTransport(){
  const playing = started && broadcasting;
  btn.disabled = playing;                     // Play is a no-op while already playing
  const pause=document.getElementById('pause');
  if(pause){
    pause.disabled = !(playing||paused);      // Pause while playing; Resume while paused
    pause.classList.toggle('active', paused); // lit amber while paused
    pause.setAttribute('aria-label', paused?'Resume':'Pause');
    pause.title = paused?'Resume':'Pause';
  }
  const stop=document.getElementById('stop'); if(stop) stop.disabled = !(playing||paused); // Stop: while playing or paused
}
async function loadBroadcast(){
  try{ broadcasting=(await (await fetch('/broadcast')).json()).broadcasting; }catch(e){}
  updateTransport();
}
// Initialise + warm the generative engine once (only needed for Flow State).
async function ensureStrudel(){
  if(strudelReady) return true;
  statusEl.textContent='starting…';
  try{ await initStrudel(); }
  catch(e){ console.error(e); statusEl.textContent='init error: '+((e&&e.message)||e); return false; }
  statusEl.textContent='warming up…';
  for(let i=0;i<80;i++){ try{ await evaluate('setcps(0.5)\ns("~")'); break; }
    catch(e){ await new Promise(r=>setTimeout(r,80)); } }
  if(typeof window.samples==='function'){
    samples('github:tidalcycles/dirt-samples').catch(e=>console.warn('samples failed:',e)); }
  strudelReady=true; return true;
}

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

// "Next update in Nm": time until the next scheduled news slot. Fetch the server's
// countdown, then tick it down locally between fetches.
let nextNewsAt=0;
function renderNextNews(){
  const el=document.getElementById('next-update'); if(!el) return;
  if(!nextNewsAt){ el.textContent=''; return; }
  const m=Math.max(0, Math.ceil((nextNewsAt-Date.now())/60000));
  el.textContent='Next update in '+m+'m';
}
async function loadNextNews(){
  try{
    const d=await (await fetch('/next-news')).json();
    nextNewsAt = d.in_s==null ? 0 : (Date.now()+d.in_s*1000);
    renderNextNews();
  }catch(e){}
}

// Quiet mode — music only around the news, silent between.
const quietBox=document.getElementById('quiet');
async function loadQuiet(){
  try{ const d=await (await fetch('/quiet')).json(); quietBox.checked=!!d.quiet_mode; }catch(e){}
}
quietBox.addEventListener('change', async ()=>{
  try{ await fetch('/quiet?on='+(quietBox.checked?'true':'false'), {method:'POST'}); await pollMusic(); }catch(e){}
});

// Demo Mode — earlier-milestone feel: HN + git issues every 2 min, music between.
const demoBox=document.getElementById('demo');
const demoStatus=document.getElementById('demo-status');
async function loadDemo(){
  try{ const d=await (await fetch('/demo')).json();
    demoBox.checked=!!d.demo_mode;
    demoStatus.textContent=d.demo_mode?'on · reading every 2 min':'';
  }catch(e){}
}
demoBox.addEventListener('change', async ()=>{
  const on=demoBox.checked;
  demoStatus.textContent=on?'starting…':'';
  try{
    const d=await (await fetch('/demo?on='+(on?'true':'false'), {method:'POST'})).json();
    demoStatus.textContent=d.demo_mode?'on · reading every 2 min':'';
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
let playerMode='flow';  // 'flow' (generative) or 'playlist' (Spotify) — the current player mode
const newsPlayer=new Audio(); let lastNewsUrl='';
// User-settable news-voice level (relative to the music), persisted.
let newsVolume=1, newsFading=false;
try{ const v=parseFloat(localStorage.getItem('smfm-newsvol')); if(!isNaN(v)) newsVolume=Math.max(0,Math.min(1,v)); }catch(e){}

// Ducking — the radio-production principles applied within the browser's limits.
//   DEPTH: the bed drops to 15% under the voice (gain 0.15 ≈ -16 dB) so the news
//     sits clearly on top, then swells back after.
//   ATTACK fast (immediate on the first syllable), RELEASE slow and musical: the
//     bed swells back ~600 ms AFTER the last word, not under it — the news tail is
//     faded so it tapers into the returning music (never a hard stop = an exit).
//   NEVER TO SILENCE: the bed keeps playing under the voice; releases overlap.
// Honest limits of @strudel/web 1.0.3: gain is set by re-evaluating the pattern
// (no master-gain automation, and a re-eval mid-note glitches), so a true ramped
// or midrange-only (1-4 kHz) sidechain isn't possible here — those, plus on-air
// processor AGC compensation, await a server-side mix. We do the full-band duck +
// a faded, delayed release, which is the audible 80%.
const DUCK={GAIN:0.15, RELEASE_MS:600, NEWS_FADE_MS:500};
let releaseTimer=null;
async function playCurrent(fresh){
  if(!currentProg) return;
  // Never sound the generative bed while Spotify/playlist is the music — otherwise
  // the news-duck path (setDuck→playCurrent) would start it *over* the playlist.
  if(spMode || playerMode==='playlist'){
    if(strudelReady && !musicSilenced){ try{ await evaluate('silence'); }catch(e){} musicSilenced=true; }
    return;
  }
  const base=currentProg.replace(/\.fadeIn\([0-9.]+\)\s*$/,'');
  const code=ducked?base+'.gain('+DUCK.GAIN+')':currentProg;
  // A genuinely NEW program (not a duck re-eval): stop the previous pattern first,
  // so an earlier generator (e.g. Space Dub) can't keep ringing under the new one.
  // evaluate() replaces the pattern, but already-triggered long samples/delays can
  // linger — hush() clears them; the new program's .fadeIn brings it back in.
  if(fresh){ try{ if(typeof hush==='function') hush(); }catch(e){} }
  // evaluate() is async; await it so a rejection is caught here (not "uncaught").
  try{ await evaluate(code); }
  catch(e){ console.error('strudel:',e); statusEl.textContent='music error: '+((e&&e.message)||e); }
}
function setDuck(on){ if(started && ducked!==on){ ducked=on; playCurrent(); } }
// Fade the news element's tail over NEWS_FADE_MS so the voice tapers out — from
// wherever the user set the level, proportionally to zero.
function fadeNewsOut(){
  newsFading=true;
  const steps=10, dt=DUCK.NEWS_FADE_MS/steps, start=newsPlayer.volume; let i=0;
  const iv=setInterval(()=>{ i++; newsPlayer.volume=Math.max(0, start*(1-i/steps));
    if(i>=steps) clearInterval(iv); }, dt);
}
newsPlayer.addEventListener('play', ()=>{
  if(releaseTimer){ clearTimeout(releaseTimer); releaseTimer=null; }
  newsFading=false; newsPlayer.volume=newsVolume; setDuck(true);   // fast attack, at the user's voice level
});
function scheduleRelease(){
  // Slow, musical release: hold the (shallow) duck a beat, let the bed swell back.
  if(releaseTimer) clearTimeout(releaseTimer);
  releaseTimer=setTimeout(()=>{ setDuck(false); releaseTimer=null; }, DUCK.RELEASE_MS);
}
// Near the end, taper the voice; on end/pause, release after the overlap window.
newsPlayer.addEventListener('timeupdate', ()=>{
  if(newsPlayer.duration && newsPlayer.duration-newsPlayer.currentTime<=DUCK.NEWS_FADE_MS/1000
     && !newsFading) fadeNewsOut();
});
// The news-voice level slider — live while a bulletin plays, and remembered.
const voiceVol=document.getElementById('voicevol');
voiceVol.value=newsVolume;
voiceVol.addEventListener('input', ()=>{
  newsVolume=parseFloat(voiceVol.value);
  try{ localStorage.setItem('smfm-newsvol', newsVolume); }catch(e){}
  if(!newsFading && !newsPlayer.paused) newsPlayer.volume=newsVolume;
});
newsPlayer.addEventListener('ended', scheduleRelease);
newsPlayer.addEventListener('pause', scheduleRelease);

// Playlist (Spotify) background volume — the music level under/between bulletins,
// relative to the news voice above. Used as the resume-after-news target and
// applied live while playing. Remembered per browser. (Only relevant in Playlist mode.)
let spVolume=0.8;
try{ const v=parseFloat(localStorage.getItem('smfm-spvol')); if(!isNaN(v)) spVolume=Math.max(0,Math.min(1,v)); }catch(e){}
const spVol=document.getElementById('spvol');
if(spVol){
  spVol.value=spVolume;
  spVol.addEventListener('input', ()=>{
    spVolume=parseFloat(spVol.value);
    try{ localStorage.setItem('smfm-spvol', spVolume); }catch(e){}
    // Apply live unless a bulletin is currently ducking the music to silence.
    if(spPlayer && spMode && !spDuckedForNews){ try{ spPlayer.setVolume(spVolume); }catch(e){} }
  });
}

let musicSilenced=false;
async function pollMusic(){
  try{
    // When the user's Spotify playlist is the music, keep the generative bed silent.
    if(spMode || playerMode==='playlist'){ if(started && !musicSilenced){ try{ await evaluate('silence'); }catch(e){} musicSilenced=true; } viz.on=false; return; }
    const d=await (await fetch('/genmusic')).json();
    // Gate: silence when the server says not to play (broadcast stopped, or quiet).
    if(started && d.play===false){
      if(!musicSilenced){ try{ await evaluate('silence'); }catch(e){} musicSilenced=true; }
      viz.on=false;
      statusEl.textContent = paused ? 'paused'
                           : broadcasting ? 'quiet · silent (music returns before the news)'
                           : 'stopped';
      return;
    }
    if(!d.text){ statusEl.textContent='waiting for activity…'; return; }
    viz.intensity=d.intensity; viz.band=d.brainwave_band; viz.on=started;
    // (re)start when the program changes OR the gate just re-opened after silence
    if(started && (d.text!==lastProgram || musicSilenced)){
      lastProgram=d.text; currentProg=d.text; musicSilenced=false; await playCurrent(true);
    }
    const ctx=(typeof getAudioContext==='function')?getAudioContext():null;
    const ac=ctx?(' · audio '+ctx.state):'';
    statusEl.textContent=(started?(ducked?'news over music':'on air'):'ready')+
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
      // No visible audio control — the bulletin auto-plays over the music (newsPlayer).
      html+='<article><h2>'+esc(s.title||'News')+'</h2>'+body+'</article>';
    }
    newsEl.innerHTML=html||'<p class="muted">No broadcast yet.</p>';
    const first=segs.find(s=>s.audio_url);
    if((started || spMode) && first && first.audio_url!==lastNewsUrl){
      lastNewsUrl=first.audio_url; newsPlayer.src=first.audio_url;
      newsPlayer.play().catch(e=>console.warn('news play:',e));
      loadNextNews();  // a bulletin just aired → re-sync the countdown to the next slot
    }
  }catch(e){}
}
// Song slot (M5): the current familiar song, embedded from Spotify when connected.
async function pollSong(){
  // When Spotify is connected, the SDK owns the "now playing" card (the real
  // current track); don't overlay the curated song-slot embeds.
  if(spConnected) return;
  try{
    const d=await (await fetch('/song')).json();
    const el=document.getElementById('song');
    if(!d || !d.title){ el.innerHTML=''; return; }
    const id=(d.uri||'').split(':').pop();
    const body = (d.source==='spotify' && id)
      ? '<iframe style="border-radius:12px;border:0" src="https://open.spotify.com/embed/track/'+
        encodeURIComponent(id)+'" width="100%" height="152" allow="autoplay; encrypted-media" loading="lazy"></iframe>'
      : (d.url ? '<a href="'+esc(d.url)+'" target="_blank" rel="noopener noreferrer">Open in Spotify</a>'
               : '<span class="muted">connect Spotify (Settings › Narration) to play this slot</span>');
    el.innerHTML='<article><h2>'+esc(d.title)+' — '+esc(d.artist)+'</h2>'+body+'</article>';
  }catch(e){}
}
// ── Spotify Web Playback SDK (Premium): play the user's playlists in this tab,
//    and fade/pause them for the news, then resume. Needs an OAuth login + Premium.
let spPlayer=null, spDevice=null, spMode=false, spDuckedForNews=false, spConnected=false;
window.onSpotifyWebPlaybackSDKReady=()=>{ window._spSdk=true; if(spConnected) initSpotifySDK(); };
async function spTok(){ try{ return (await (await fetch('/spotify/token')).json()).access_token; }catch(e){ return null; } }
function spMsg(t){ document.getElementById('sp-msg').textContent=t; }
function spSetReady(ready){
  document.getElementById('sp-skip').disabled=!ready;
}
async function loadSpotifyBar(){
  const bar=document.getElementById('spotify-bar');
  try{
    if(!(await (await fetch('/spotify')).json()).configured){ bar.hidden=true; return; }
    bar.hidden=false;
    const me=await (await fetch('/spotify/me')).json(); spConnected=!!me.connected;
    document.getElementById('sp-connect').hidden=spConnected;
    document.getElementById('sp-logout').hidden=!spConnected;
    document.getElementById('sp-playlist').hidden=!spConnected;
    document.getElementById('sp-skip').hidden=!spConnected;
    document.getElementById('spvolwrap').hidden=!spConnected;
    document.getElementById('sp-who').textContent = spConnected
      ? (esc(me.name)+(me.premium?' · Premium':' · NOT Premium — in-tab playback needs Premium')) : '';
    // Only reset the ready state when there's no player yet — re-running this (e.g.
    // on a tab switch) must not flip an already-ready player back to "not ready".
    if(spConnected){ if(!spPlayer) spSetReady(false); await loadPlaylists(); initSpotifySDK(); }
  }catch(e){ bar.hidden=true; }
}
async function loadPlaylists(){
  try{ const d=await (await fetch('/spotify/playlists')).json();
    const sel=document.getElementById('sp-playlist'); sel.innerHTML='';
    for(const p of (d.playlists||[])){ const o=document.createElement('option');
      o.value=p.uri; o.textContent=p.name+(p.tracks?(' ('+p.tracks+')'):''); sel.appendChild(o); }
  }catch(e){}
}
let spErrShown=false;
async function spCheckDRM(){
  try{
    if(!navigator.requestMediaKeySystemAccess) return 'no EME/DRM support in this browser';
    await navigator.requestMediaKeySystemAccess('com.widevine.alpha',
      [{initDataTypes:['cenc'], audioCapabilities:[{contentType:'audio/mp4;codecs="mp4a.40.2"'}]}]);
    return null;  // Widevine available
  }catch(e){ return 'Widevine DRM is not available/enabled'; }
}
function initSpotifySDK(){
  if(spPlayer) return;
  if(!window.Spotify){ spMsg('loading Spotify player…');
    setTimeout(()=>{ if(!window.Spotify) spMsg('Spotify player SDK did not load — check network / ad-blocker / Brave Shields'); }, 9000);
    return; }
  spErrShown=false;
  spCheckDRM().then(p=>{ if(p){ spErrShown=true;
    spMsg('⚠ '+p+'. Spotify playback needs Widevine. In Brave: brave://settings/extensions → enable Widevine, and drop Shields for this site + spotify.com; or use Chrome.'); } });
  spMsg('connecting the player…');
  spPlayer=new Spotify.Player({name:'State Media FM', volume:0.8,
    getOAuthToken: cb=>{ spTok().then(t=>cb(t||'')); }});
  const err=(label)=>({message})=>{ spErrShown=true; spMsg(label+': '+(message||'')); };
  spPlayer.addListener('ready', ({device_id})=>{ spDevice=device_id; spSetReady(true); spErrShown=true; spMsg('player ready — press Play'); });
  spPlayer.addListener('not_ready', ()=>{ spDevice=null; spSetReady(false); spMsg('device went offline'); });
  spPlayer.addListener('initialization_error', err('init error (usually Widevine/DRM in Brave)'));
  spPlayer.addListener('authentication_error', err('auth error — Disconnect then Connect'));
  spPlayer.addListener('account_error', err('account error (Premium required)'));
  spPlayer.addListener('playback_error', err('playback error'));
  // Show the ACTUAL current track (real album art), not a disconnected pick.
  spPlayer.addListener('player_state_changed', s=>{
    const el=document.getElementById('song');
    if(!el) return;
    const t = s && s.track_window && s.track_window.current_track;
    if(!t){ el.innerHTML=''; return; }
    const img=(t.album&&t.album.images&&t.album.images[0])?t.album.images[0].url:'';
    const art=(t.artists||[]).map(a=>a.name).join(', ');
    el.innerHTML='<article><h2>'+(s.paused?'paused — ':'')+esc(t.name)+' — '+esc(art)+'</h2>'+
      (img?'<img src="'+esc(img)+'" alt="" style="width:128px;height:128px;border-radius:8px">':'')+'</article>';
  });
  spPlayer.connect().then(ok=>{ if(!ok){ spErrShown=true; spMsg('player.connect() was rejected'); } });
  setTimeout(()=>{ if(!spDevice && !spErrShown) spMsg('never became ready — in Brave this is almost always Widevine/Shields; enable Widevine or try Chrome'); }, 12000);
}
async function spPlay(){
  const uri=document.getElementById('sp-playlist').value;
  if(!uri){ spMsg('pick a playlist first'); return; }
  if(!spDevice){ spMsg('player not ready yet — see status'); initSpotifySDK(); return; }
  const t=await spTok();
  const H={'Authorization':'Bearer '+t,'Content-Type':'application/json'};
  try{
    // Make this tab the active device, then start the playlist on it.
    await fetch('https://api.spotify.com/v1/me/player',
      {method:'PUT', headers:H, body:JSON.stringify({device_ids:[spDevice], play:false})});
    const r=await fetch('https://api.spotify.com/v1/me/player/play?device_id='+encodeURIComponent(spDevice),
      {method:'PUT', headers:H, body:JSON.stringify({context_uri:uri})});
    if(!r.ok){ const b=await r.text(); spMsg('play failed ('+r.status+') '+b.slice(0,140)); return; }
    spMode=true; try{ if(strudelReady) await evaluate('silence'); }catch(e){}  // Spotify is the music now
    try{ if(spPlayer) await spPlayer.setVolume(spVolume); }catch(e){}  // at the user's music level
    spMsg('playing your playlist');
  }catch(e){ spMsg('play error: '+((e&&e.message)||e)); }
}
async function spStop(){
  spMode=false; try{ if(spPlayer) await spPlayer.pause(); }catch(e){}
  spMsg('stopped — back to the generative bed');
  lastProgram=''; pollMusic();  // bring the generative bed back
}
document.getElementById('sp-connect').addEventListener('click', ()=>{ window.location='/spotify/login'; });
document.getElementById('sp-logout').addEventListener('click', async ()=>{
  await fetch('/spotify/logout',{method:'POST'}); try{ if(spPlayer) spPlayer.disconnect(); }catch(e){}
  spPlayer=null; spMode=false; loadSpotifyBar(); });
document.getElementById('sp-skip').addEventListener('click', async ()=>{
  if(!spDevice) return;
  const t=await spTok();
  try{ await fetch('https://api.spotify.com/v1/me/player/next?device_id='+encodeURIComponent(spDevice),
    {method:'POST', headers:{'Authorization':'Bearer '+t}}); spMsg('skipped'); }
  catch(e){ spMsg('skip failed'); }
});
// Change the playlist live: if we're already playing, switch to the newly chosen
// one right away; otherwise the new selection just applies on the next Play.
document.getElementById('sp-playlist').addEventListener('change', ()=>{
  if(spMode) spPlay();
});
// Fade the Spotify music down and pause it for the news, then resume + fade up.
function spFade(to, ms, then){
  if(!spPlayer){ if(then) then(); return; }
  spPlayer.getVolume().then(v0=>{ const steps=8, dt=Math.max(20, ms/steps); let i=0;
    const iv=setInterval(()=>{ i++; spPlayer.setVolume(Math.max(0, Math.min(1, v0+(to-v0)*(i/steps))));
      if(i>=steps){ clearInterval(iv); if(then) then(); } }, dt); });
}
newsPlayer.addEventListener('play', ()=>{ if(spMode){ spDuckedForNews=true;
  spFade(0.0, 500, ()=>{ try{ spPlayer.pause(); }catch(e){} }); } });
function spResumeAfterNews(){ if(spDuckedForNews){ spDuckedForNews=false;
  setTimeout(()=>{ try{ spPlayer.resume(); }catch(e){}; spFade(spVolume, 700); }, 600); } }
newsPlayer.addEventListener('ended', spResumeAfterNews);
newsPlayer.addEventListener('pause', spResumeAfterNews);
// Player modes: reveal the Flow State (generative) or Playlist (Spotify) controls.
function setPlayerMode(m){
  playerMode=m;
  document.getElementById('flow-panel').hidden = m!=='flow';
  document.getElementById('playlist-panel').hidden = m!=='playlist';
  document.querySelectorAll('#modes button').forEach(b=>b.classList.toggle('active', b.dataset.mode===m));
  try{ localStorage.setItem('smfm-mode', m); }catch(e){}
  if(m==='playlist'){
    // Switch away from the generative Flow music immediately; ready the Spotify player.
    if(strudelReady){ try{ evaluate('silence'); }catch(e){} musicSilenced=true; }
    viz.on=false;
    loadSpotifyBar();
  }else{
    // Flow: stop Spotify and bring the generative bed back (if we're playing).
    if(spMode){ spStop(); } else if(started && broadcasting){ lastProgram=''; musicSilenced=false; pollMusic(); }
  }
  updateTransport();
}
document.querySelectorAll('#modes button').forEach(b=>
  b.addEventListener('click', ()=>setPlayerMode(b.dataset.mode)));
// Start (or resume from Pause) the radio in the current mode — shared by the Play
// button and Pause's resume. Flow State starts the generative bed; Playlist starts
// your Spotify playlist. The news airs over whichever you pick.
async function startBroadcast(resuming){
  paused=false;
  try{
    broadcasting=true;
    try{ await fetch('/broadcast?on=true',{method:'POST'}); }catch(e){}
    if(playerMode==='playlist'){
      if(!spConnected){ statusEl.textContent='connect Spotify below, then press Play';
        spMsg('Connect Spotify to play a playlist'); broadcasting=false; return; }
      started=true; pollNews(); pollSong();
      // Resume where Pause left off if we can; otherwise (re)start the playlist.
      if(resuming && spPlayer){ try{ await spPlayer.resume(); spMode=true; }catch(e){ await spPlay(); } }
      else{ await spPlay(); }  // starts your playlist; spMode=true. News ducks it.
    }else{
      if(!(await ensureStrudel())){ broadcasting=false; return; }
      started=true;
      await pollMusic(); pollNews(); pollSong();
    }
  } finally { updateTransport(); }
}
btn.addEventListener('click', async ()=>{ btn.disabled=true; await startBroadcast(paused); });
// Pause: stop the music + broadcast but keep the session so it can resume. It
// TOGGLES — pressed while playing it pauses (and lights amber); pressed again it
// resumes. Play resumes too. Unlike Stop, spMode is kept so Spotify resumes in place.
async function pauseBroadcast(){
  broadcasting=false; paused=true;
  try{ await fetch('/broadcast?on=false',{method:'POST'}); }catch(e){}  // stops the server loop + silences music
  if(spMode){ try{ if(spPlayer) await spPlayer.pause(); }catch(e){} }
  else if(strudelReady){ try{ await evaluate('silence'); }catch(e){} musicSilenced=true; }
  viz.on=false; statusEl.textContent='paused';
  updateTransport();
}
document.getElementById('pause').addEventListener('click', async ()=>{
  if(paused){ await startBroadcast(true); }                 // pressed while paused → resume
  else if(started && broadcasting){ await pauseBroadcast(); }  // pressed while playing → pause
});
// Stop: halt the radio (music/playlist + news) and reset. Play restarts.
document.getElementById('stop').addEventListener('click', async ()=>{
  broadcasting=false; paused=false;
  try{ await fetch('/broadcast?on=false',{method:'POST'}); }catch(e){}
  if(spMode){ spMode=false; try{ if(spPlayer) await spPlayer.pause(); }catch(e){} }
  else if(strudelReady){ try{ await evaluate('silence'); }catch(e){} musicSilenced=true; }
  viz.on=false; statusEl.textContent='stopped';
  updateTransport();
});
// Newscast now: air a bulletin from the latest activity on demand, without
// re-polling sources or resetting any timer (see POST /news-now). Force the news
// audio to play by clearing lastNewsUrl so an identical bulletin still airs.
document.getElementById('news-now').addEventListener('click', async ()=>{
  const b=document.getElementById('news-now'), s=document.getElementById('status');
  b.disabled=true; s.textContent='airing newscast…';
  try{
    const r=await (await fetch('/news-now',{method:'POST'})).json();
    if(r.aired){ lastNewsUrl=''; await pollNews();
      s.textContent = started ? 'newscast' : 'newscast ready — press Play to hear it'; }
    else{ s.textContent='no activity yet to report'; }
  }catch(e){ s.textContent='error'; }
  setTimeout(()=>{ b.disabled=false; }, 2500);
});
// Local network access: opt-in bind to a LAN address (applies on the next start).
async function loadInterfaces(){
  const en=document.getElementById('lan-enabled'), sel=document.getElementById('lan-host'),
        st=document.getElementById('lan-status'), any=document.getElementById('lan-anyhost');
  if(!en||!sel) return;
  try{
    const d=await (await fetch('/interfaces')).json();
    const lan=(d.addresses||[]).filter(a=>a!=='127.0.0.1');
    sel.innerHTML=lan.map(a=>'<option'+(a===d.selected?' selected':'')+'>'+esc(a)+'</option>').join('');
    en.checked=!!d.enabled; en.disabled=lan.length===0;
    sel.disabled=!d.enabled||lan.length===0;
    if(any) any.checked=!!d.allow_any_host;
    st.textContent = lan.length ? ('serving on '+esc(d.bound)) : 'no LAN address found on this host';
  }catch(e){}
}
document.getElementById('lan-anyhost').addEventListener('change', async (e)=>{
  const st=document.getElementById('lan-anyhost-status');
  try{
    await fetch('/allow-any-host?on='+(e.target.checked?'true':'false'), {method:'POST'});
    st.textContent = e.target.checked
      ? 'any host allowed — reachable via tunnel/proxy/domain (token still required)'
      : 'reach it via a tunnel, reverse proxy, or public domain (applies immediately)';
  }catch(err){ st.textContent='error'; }
});
async function saveInterfaces(){
  const en=document.getElementById('lan-enabled'), sel=document.getElementById('lan-host'),
        st=document.getElementById('lan-status');
  const enabled=en.checked, host=sel.value;
  sel.disabled=!enabled;
  try{
    const r=await fetch('/interfaces?enabled='+(enabled?'true':'false')+'&host='+encodeURIComponent(host),
      {method:'POST'});
    const d=await r.json().catch(()=>({}));
    if(!r.ok){ st.textContent='error: '+(d.detail||r.status); en.checked=!enabled; sel.disabled=!en.checked; return; }
    st.textContent = d.restart_required
      ? ('saved — restart to bind '+esc(d.selected)+' (currently on '+esc(d.bound)+')')
      : ('serving on '+esc(d.bound));
  }catch(e){ st.textContent='error'; }
}
document.getElementById('lan-enabled').addEventListener('change', saveInterfaces);
document.getElementById('lan-host').addEventListener('change', saveInterfaces);

// Tabs: Player / Settings.
function esc(s){ return String(s==null?'':s).replace(/[&<>"]/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c])); }
// The Premium section is hidden unless the server enables it (config/env flag).
if(__SMFM_SHOW_PREMIUM__){ const _p=document.getElementById('sec-premium'); if(_p) _p.hidden=false; }
document.querySelectorAll('#tabs a').forEach(a=>a.addEventListener('click', ()=>{
  document.querySelectorAll('#tabs a').forEach(x=>x.classList.toggle('active', x===a));
  const tab=a.dataset.tab;
  document.getElementById('player-view').hidden = tab!=='player';
  document.getElementById('settings-view').hidden = tab!=='settings';
  if(tab==='settings'){ loadDemo(); loadCadence(); loadSources(); loadNarration(); loadSpotify(); loadNewsBackend(); loadPresets(); loadAuth(); loadGateways(); loadGatewayModels(false); loadInterfaces(); loadTheme(); }
  // Returning to the player re-syncs it with any settings just changed, so nothing
  // needs a full reload (all of these are idempotent reads).
  if(tab==='player'){ loadSpotifyBar(); loadBroadcast(); loadQuiet(); loadIntensity(); loadNextNews(); pollMusic(); pollSong(); }
}));

// ── Voice selection (feature-flagged off for now) ─────────────────────────────
// Themed persona selection has been removed from Settings; voice selection is
// hidden behind this flag until it's ready to surface again. Flip to re-enable.
const FEATURE_VOICE_SELECT=false;
async function loadNarration(){
  const row=document.getElementById('voice-row');
  if(row) row.hidden=!FEATURE_VOICE_SELECT;
  if(!FEATURE_VOICE_SELECT) return;
  try{
    const v=await (await fetch('/voice')).json();
    const sel=document.getElementById('voice-sel'); sel.innerHTML='';
    for(const x of (v.voices||[])){ const o=document.createElement('option'); o.value=x; o.textContent=x;
      if(x===v.current) o.selected=true; sel.appendChild(o); }
  }catch(e){}
}
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
    await loadNarration();
  }catch(e){ st.textContent='error'; }
});
// Mix (under Narration) — rotate ambient generators, and/or mix in Spotify songs.
// Spotify connector (under Mix) — Client ID + Secret, saved gitignored.
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
    loadSpotifyBar();  // reveal the Connect bar on the player without a reload
  }catch(e){ st.textContent='error'; }
});
document.getElementById('sp-test').addEventListener('click', async ()=>{
  const st=document.getElementById('sp-status'); st.textContent='testing…';
  try{
    const d=await (await fetch('/spotify/test',{method:'POST'})).json();
    st.textContent = d.ok ? 'connection OK' : ('failed: '+(d.detail||'unknown'));
  }catch(e){ st.textContent='error'; }
});
document.getElementById('narration-save').addEventListener('click', async ()=>{
  const st=document.getElementById('narration-status'); st.textContent='saving…';
  const voice=document.getElementById('voice-sel').value;
  try{
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
  {label:'GitLab work items (URL)', kind:'repo', key:'repo',
   ph:'https://gitlab.com/group/project  (a group /groups/… also works, or an issue/MR URL)'},
  {label:'GitLab to-dos (@mentions)', kind:'repo', key:'repo',
   ph:'https://gitlab.com/dashboard/todos  (or https://gitlab.mycorp.com/dashboard/todos)'},
  {label:'GitHub Enterprise work items (URL)', kind:'repo', key:'repo',
   ph:'https://ghe.yourcompany.com/owner/repo  (set the GitHub endpoint under Auth first)'},
  {label:'GitHub Enterprise mentions (@me)', kind:'repo', key:'repo',
   ph:'https://ghe.yourcompany.com/issues?q=is:issue+state:open+mentions:@me'},
  {label:'GitHub work items (URL)', kind:'repo', key:'repo',
   ph:'https://github.com/owner/repo  (or an issue/PR URL)'},
  {label:'GitHub mentions (@me)', kind:'repo', key:'repo',
   ph:'https://github.com/issues?q=is:issue+state:open+mentions:@me'},
  {label:'Hacker News', kind:'hackernews', key:null, ph:null},
  {label:'Slack channel', kind:'slack', key:'channel', ph:'channel name or ID'},
  {label:'Jira project', kind:'jira', key:'project', ph:'project key, e.g. OPS'},
  {label:'PagerDuty', kind:'pagerduty', key:'statuses',
   ph:'statuses (comma-sep), e.g. triggered,acknowledged'},
];
const srcKind=document.getElementById('src-kind');
const srcParam=document.getElementById('src-param');
let addOptions=[];  // ADD_OPTIONS plus any extra server kinds (e.g. plugins)
let editingIndex=null;  // the source index being edited, or null when adding
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
      const cfg=s.config||{}; const on=s.enabled!==false;
      if(!on) row.classList.add('off');
      const extra=[cfg.headlines!=null?('headlines '+cfg.headlines):'',
                   cfg.max_count!=null?('max '+cfg.max_count):'',
                   cfg.max_age!=null?('≤'+cfg.max_age):''].filter(Boolean).join(' · ');
      row.innerHTML='<label class="switch" title="on/off"><input type="checkbox" class="src-on"'+
          (on?' checked':'')+'><span class="track"></span></label>'+
        '<span class="kind">'+esc(s.kind||'?')+'</span>'+
        '<span class="grow">'+esc(s.topic||'')+' <span class="muted">· every '+esc(s.every)+
        (extra?(' · '+esc(extra)):'')+(on?'':' · off')+'</span></span>'+
        '<select class="src-voice" aria-label="voice"></select>'+
        '<span class="muted src-result"></span>'+
        '<button class="src-test">Test</button>'+
        '<button class="src-edit">Edit</button>'+
        '<button class="src-remove">Remove</button>';
      const res=row.querySelector('.src-result');
      // Per-source voice: a COMPACT widget so it never widens/wraps the row — it
      // shows just the speaker's initial when closed, and the full names only in the
      // open menu. "Random" (auto rotation) is the default.
      const vsel=row.querySelector('.src-voice'); const curV=s.voice||'random';
      const _init=x=>(String(x||'').charAt(0).toUpperCase()||'·');
      const opts=[['random','Random']].concat((d.voices||[]).map(v=>[v,v]));
      vsel.innerHTML=opts.map(([val,lab])=>
        '<option value="'+esc(val)+'" data-full="'+esc(lab)+'">'+esc(lab)+'</option>').join('');
      vsel.value=curV;
      const _expand=()=>{ for(const o of vsel.options) o.textContent=o.dataset.full; };  // open → full names
      const _collapse=()=>{                                                            // closed → initial only
        const sel=vsel.selectedOptions[0];
        for(const o of vsel.options) o.textContent=o.dataset.full;
        if(sel) sel.textContent=_init(sel.dataset.full);
        vsel.title='voice: '+(sel?sel.dataset.full:'Random');
      };
      _collapse();
      vsel.addEventListener('mousedown', _expand);  // about to open
      vsel.addEventListener('focus', _expand);
      vsel.addEventListener('blur', _collapse);
      vsel.addEventListener('change', async (e)=>{
        _collapse();
        try{ await fetch('/sources/'+s.index+'/voice?voice='+encodeURIComponent(e.target.value),
          {method:'POST'}); }catch(err){}
      });
      row.querySelector('.src-on').addEventListener('change', async (e)=>{
        try{ await fetch('/sources/'+s.index+'/enabled?on='+(e.target.checked?'true':'false'),
          {method:'POST'}); await loadSources(); }catch(err){ await loadSources(); }
      });
      row.querySelector('.src-test').addEventListener('click', async (e)=>{
        const b=e.target; b.disabled=true; res.textContent='testing…';
        try{
          const r=await (await fetch('/sources/'+s.index+'/test',{method:'POST'})).json();
          res.textContent = r.ok ? ('OK · '+r.count+' item'+(r.count===1?'':'s'))
                                 : ('error: '+(r.detail||('status '+r.status)));
        }catch(err){ res.textContent='error'; }
        b.disabled=false;
      });
      row.querySelector('.src-edit').addEventListener('click', ()=>startEditSource(s));
      row.querySelector('.src-remove').addEventListener('click', async ()=>{
        try{ await fetch('/sources/'+s.index,{method:'DELETE'}); await loadSources(); }catch(e){}
      });
      list.appendChild(row);
    }
    if(!(d.sources||[]).length) list.innerHTML='<p class="muted">No sources yet.</p>';
  }catch(e){ list.textContent='Could not load sources.'; }
}
// Populate the form with an existing source's config and switch to edit mode.
function startEditSource(s){
  const cfg=s.config||{};
  // Pick the first add-option matching this kind (repo has GitHub/GitLab variants).
  let optIdx=addOptions.findIndex(o=>o.kind===s.kind);
  if(optIdx<0) optIdx=0;
  srcKind.value=optIdx; updateSrcPlaceholder();
  const opt=currentAddOption();
  if(opt.key){ const v=cfg[opt.key];
    srcParam.value = Array.isArray(v) ? v.join(',') : (v!=null?String(v):''); }
  const set=(id,v)=>{ document.getElementById(id).value = v!=null?String(v):''; };
  set('src-topic', cfg.topic); set('src-every', cfg.every || '15m');
  set('src-headlines', cfg.headlines); set('src-maxcount', cfg.max_count);
  set('src-offset', cfg.offset); set('src-maxage', cfg.max_age);
  editingIndex=s.index;
  document.getElementById('src-add').textContent='Save changes';
  document.getElementById('src-cancel').hidden=false;
  document.getElementById('src-status').textContent='editing '+(cfg.topic||s.kind);
  srcKind.disabled=true;  // keep the kind stable while editing; Remove+re-add to change it
  document.getElementById('src-topic').scrollIntoView({block:'nearest'});
}
function resetSourceForm(){
  editingIndex=null;
  document.getElementById('src-add').textContent='Add source';
  document.getElementById('src-cancel').hidden=true;
  srcKind.disabled=false;
  for(const id of ['src-param','src-maxage','src-topic','src-headlines','src-maxcount','src-offset'])
    document.getElementById(id).value='';
  document.getElementById('src-every').value='15m';
  document.getElementById('src-status').textContent='';
}
document.getElementById('src-cancel').addEventListener('click', resetSourceForm);
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
  // PUT to replace when editing an existing source, else POST to add a new one.
  const editing = editingIndex!=null;
  const url = editing ? ('/sources/'+editingIndex) : '/sources';
  st.textContent = editing ? 'saving…' : 'adding…';
  try{
    const r=await fetch(url,{method: editing?'PUT':'POST',
      headers:{'Content-Type':'application/json'}, body:JSON.stringify(seg)});
    if(!r.ok){ const e=await r.json().catch(()=>({})); st.textContent='error: '+(e.detail||r.status); return; }
    resetSourceForm(); await loadSources();
  }catch(e){ st.textContent='error'; }
});

// News writer: local Claude CLI vs the LLM gateway.
async function loadNewsBackend(){
  try{
    const d=await (await fetch('/news-backend')).json();
    const sel=document.getElementById('news-backend'); if(sel) sel.value=d.backend;
    const st=document.getElementById('news-backend-status');
    if(st) st.textContent = d.backend==='claude-cli'
      ? (d.claude_available ? '· uses your logged-in Claude Code (no API key)'
         : '· claude CLI not found on PATH — install Claude Code, or use the gateway')
      : '· uses the LLM gateway below';
  }catch(e){}
}
document.getElementById('news-backend').addEventListener('change', async (e)=>{
  try{ await fetch('/news-backend?backend='+encodeURIComponent(e.target.value), {method:'POST'});
    await loadNewsBackend(); }catch(err){}
});

// ── Theme (app-wide look, saved per browser) ─────────────────────────────────
// Only the default is styled today; the rest are stubs applied via <html
// data-theme="…"> so their CSS can be filled in later without more wiring.
const THEME_NAMES={adequate:'Adequate',analog:'Analog',vapor:'Vapor'};
function currentTheme(){ try{ return localStorage.getItem('smfm-theme')||'adequate'; }catch(e){ return 'adequate'; } }
function applyTheme(t){ document.documentElement.dataset.theme = THEME_NAMES[t]?t:'adequate'; }
function loadTheme(){
  const t=currentTheme(); applyTheme(t);
  const sel=document.getElementById('theme-sel'); if(sel) sel.value=t;
}
document.getElementById('theme-sel').addEventListener('change', (e)=>{
  const t=THEME_NAMES[e.target.value]?e.target.value:'adequate';
  try{ localStorage.setItem('smfm-theme', t); }catch(err){}
  applyTheme(t);
});
applyTheme(currentTheme());  // apply immediately on load, before the Settings tab opens

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
        b.textContent=p.name+' — set the API key, then Save';
      });
      wrap.appendChild(b);
    }
  }catch(e){}
}
// Where each provider issues an auth token — shown as a "?" mouseover hint so
// you don't have to hunt for the setting.
const TOKEN_PATHS={
  github:'GitHub: Settings → Developer settings → Personal access tokens → Fine-grained tokens (read-only)',
  gitlab:'GitLab: Preferences → Access Tokens → Personal Access Tokens (scope: read_api)',
  jira:'Jira: Atlassian account → Security → Create and manage API tokens',
  slack:'Slack: api.slack.com/apps → your app → OAuth & Permissions → Bot/User OAuth Token',
  pagerduty:'PagerDuty: User Settings → My Profile → User Settings → Create API User Token',
  'llm-gateway':"Your gateway's dashboard → API Keys"
};
// A single endpoint/token row, shared by the Auth (news sources) and Gateways
// sections — both POST to /auth; the placeholder differs (endpoint vs URL).
function authRow(src, c, epPlaceholder){
  const row=document.createElement('div'); row.className='authrow'; row.dataset.source=src;
  const path=TOKEN_PATHS[src];
  row.innerHTML='<strong>'+esc(src)+'</strong>'+
    (path?' <span class="help" title="'+esc(path)+'">?</span>':'')+
    ' <span class="muted">'+
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
// Cadence: news-bulletin + source-poll intervals (live, persisted).
function _fmtSecs(s){ return s==null ? '' : (s%60===0 ? (s/60)+'m' : s+'s'); }
async function loadCadence(){
  try{
    const d=await (await fetch('/cadence')).json();
    document.getElementById('cad-news').value=_fmtSecs(d.news_every_s);
    document.getElementById('cad-refresh').value=_fmtSecs(d.refresh_s);
  }catch(e){}
}
document.getElementById('cad-save').addEventListener('click', async ()=>{
  const st=document.getElementById('cad-status'); st.textContent='saving…';
  const news=document.getElementById('cad-news').value.trim();
  const refresh=document.getElementById('cad-refresh').value.trim();
  const q=[];
  if(news) q.push('news_every='+encodeURIComponent(news));
  if(refresh) q.push('refresh='+encodeURIComponent(refresh));
  if(!q.length){ st.textContent=''; return; }
  try{
    const r=await fetch('/cadence?'+q.join('&'), {method:'POST'});
    if(!r.ok){ const e=await r.json().catch(()=>({})); st.textContent='error: '+(e.detail||r.status); return; }
    await loadCadence(); st.textContent='applied';
  }catch(e){ st.textContent='error'; }
});
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
// LLM-gateway model picker. `probe` hits the gateway's /models live (also the
// gateway test); without it we just restore the last-known list + selection.
function _fillModels(sel, models, selected){
  const cur=selected||'';
  sel.innerHTML='<option value="">— none (no bulletins until set) —</option>'
    + (models||[]).map(m=>'<option'+(m===cur?' selected':'')+'>'+esc(m)+'</option>').join('');
  if(cur && !(models||[]).includes(cur))  // keep a configured-but-unlisted model visible
    sel.insertAdjacentHTML('beforeend','<option value="'+esc(cur)+'" selected>'+esc(cur)+' (configured)</option>');
}
async function loadGatewayModels(probe){
  const sel=document.getElementById('gw-model'), st=document.getElementById('gw-model-status');
  if(!sel) return;
  if(probe) st.textContent='testing gateway…';
  try{
    const d=await (await fetch('/gateway-models'+(probe?'':'?probe=0'))).json();
    _fillModels(sel, d.models, d.selected);
    if(probe) st.textContent = d.ok ? ('✓ gateway OK — '+(d.models||[]).length+' models')
                                    : ('✗ '+(d.error||'gateway error'));
    else st.textContent='';
  }catch(e){ if(probe) st.textContent='✗ request failed'; }
}
document.getElementById('gw-load').addEventListener('click', ()=>loadGatewayModels(true));
document.getElementById('gw-model').addEventListener('change', async (e)=>{
  const st=document.getElementById('gw-model-status');
  try{ await fetch('/news-model?model='+encodeURIComponent(e.target.value), {method:'POST'});
    st.textContent = e.target.value ? ('model set: '+e.target.value) : 'model cleared';
  }catch(err){ st.textContent='could not set model'; }
});
loadModels(); loadTunings(); loadQuiet(); loadIntensity(); loadBroadcast(); loadNextNews(); pollMusic(); pollNews(); pollSong(); loadSpotifyBar();
setPlayerMode((function(){ try{ return localStorage.getItem('smfm-mode')||'flow'; }catch(e){ return 'flow'; } })());
setInterval(pollMusic, 8000);
setInterval(pollNews, 15000);
setInterval(pollSong, 15000);
setInterval(loadNextNews, 30000);  // re-sync the countdown
setInterval(renderNextNews, 15000);  // tick it down between syncs

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
<script src='https://sdk.scdn.co/spotify-player.js'></script>
"""
