"""Assemble pillar outputs into a BroadcastPlan.

M1 builds the simplest possible plan — a single news segment. The
rhythm-of-the-day scheduler (news cadence, music between, song slots) layers on
top of this in M4.
"""

from __future__ import annotations

from datetime import datetime

from .models import AudioRef, BroadcastPlan, Script, Segment


def single_news_plan(
    audio: AudioRef,
    script: Script,
    *,
    tenant: str | None = None,
    start_s: float = 0.0,
    window_start: datetime | None = None,
    window_end: datetime | None = None,
) -> BroadcastPlan:
    """Wrap one voiced news clip as a one-segment plan."""
    segment = Segment(
        kind="news",
        start_s=start_s,
        duration_s=audio.duration_ms / 1000.0,
        audio=audio,
        script=script,
    )
    return BroadcastPlan(
        segments=[segment],
        tenant=tenant,
        window_start=window_start,
        window_end=window_end,
    )


def plan_to_dict(plan: BroadcastPlan) -> dict:
    """JSON-serializable view of a plan. Audio bytes are referenced by id, not
    inlined — the web layer serves them from ``/audio/{id}``."""
    return {
        "tenant": plan.tenant,
        "window_start": plan.window_start.isoformat() if plan.window_start else None,
        "window_end": plan.window_end.isoformat() if plan.window_end else None,
        "segments": [
            {
                "kind": s.kind,
                "title": s.title,
                "start_s": s.start_s,
                "duration_s": s.duration_s,
                "audio_id": s.audio.id if s.audio else None,
                "audio_url": f"/audio/{s.audio.id}" if s.audio else None,
                "script": s.script.text if s.script else None,
                "style": s.script.style if s.script else None,
                "headlines": [{"title": t, "url": u} for t, u in s.headlines],
            }
            for s in plan.segments
        ],
    }
