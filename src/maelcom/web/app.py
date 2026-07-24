"""FastAPI app for the M1 slice: /health, /plan, /audio/{id}, and a minimal page.

The web layer is deliberately thin (plan §5.6): it holds the latest plan + an
in-memory audio store and renders/serves them. It contains no summarization or
scheduling logic. FastAPI is imported lazily so the rest of the package (and the
CLI/tests) work without the web dependency installed.
"""

from __future__ import annotations

from ..core.models import AudioRef, BroadcastPlan, StrudelProgram
from ..core.plan import plan_to_dict


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

    def set_plan(self, plan: BroadcastPlan) -> None:
        self.plan = plan
        self.audio = {s.audio.id: s.audio for s in plan.segments if s.audio}

    def set_program(self, program: StrudelProgram) -> None:
        self.program = program


def create_app(state: _State | None = None):
    """Build the FastAPI application. Call ``app.state.store.set_plan(...)`` to
    publish a plan for the page and API to serve."""
    from fastapi import FastAPI, HTTPException, Response
    from fastapi.responses import HTMLResponse

    store = state or _State()
    app = FastAPI(title="Maelcom", version="0.1.0")
    app.state.store = store

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
            return {"text": None}
        return program_to_dict(store.program)

    @app.get("/audio/{clip_id}")
    def audio(clip_id: str) -> Response:
        clip = store.audio.get(clip_id)
        if clip is None:
            raise HTTPException(status_code=404, detail="unknown clip")
        return Response(content=clip.data, media_type=clip.media_type)

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return _render_page(store)

    return app


def _render_page(store: _State) -> str:
    """A minimal Edward-Tufte-flavored page: headline, script, and a player."""
    seg = store.plan.segments[0] if store.plan and store.plan.segments else None
    if seg is None or seg.script is None:
        body = "<p class='muted'>No broadcast yet.</p>"
    else:
        audio_tag = (
            f"<audio controls src='/audio/{seg.audio.id}'></audio>" if seg.audio else ""
        )
        body = (
            f"<p class='muted'>{seg.script.style} · {seg.duration_s:.0f}s</p>"
            f"{audio_tag}"
            f"<p class='script'>{seg.script.text}</p>"
        )
    return (
        "<!doctype html><meta charset='utf-8'><title>Maelcom</title>"
        "<style>"
        "body{max-width:42rem;margin:6vh auto;padding:0 1.25rem;"
        "font:16px/1.55 Georgia,'Times New Roman',serif;color:#111;background:#fffff8}"
        "h1{font-weight:normal;letter-spacing:.02em}"
        ".muted{color:#666;font-size:.85rem;font-style:italic}"
        ".script{margin-top:1rem}audio{width:100%;margin:.5rem 0}"
        "@media(prefers-color-scheme:dark){body{background:#111;color:#eee}.muted{color:#aaa}}"
        "</style>"
        "<h1>Maelcom</h1>" + body
    )
