"""Tests for the rhythm-of-the-day scheduler (cadence → timed segments)."""

from __future__ import annotations

import pytest

from maelcom.core.models import AudioRef, Script
from maelcom.core.schedule import Cadence, Programme, assemble_broadcast, build_rundown


def test_cadence_slots_in_window():
    c = Cadence(every_s=900, offset_s=360)  # every 15 min, offset 6 min
    assert c.slots_in(0, 3600) == [360, 1260, 2160, 3060]
    # Half-open: start included, end excluded.
    assert c.slots_in(360, 1260) == [360]


def test_cadence_requires_positive_interval():
    with pytest.raises(ValueError):
        Cadence(every_s=0).slots_in(0, 100)


def test_build_rundown_interleaves_and_orders_by_time():
    programmes = [
        Programme("Hacker News front page", Cadence(900, 360)),
        Programme("Repository activity", Cadence(900, 0.0)),
    ]
    run = build_rundown(programmes, window_s=1800)
    assert run == [
        (0.0, "Repository activity"),
        (360.0, "Hacker News front page"),
        (900.0, "Repository activity"),
        (1260.0, "Hacker News front page"),
    ]


def _voiced(text: str, ms: int) -> tuple[Script, AudioRef]:
    return Script(text=text, style="x"), AudioRef(id="a", duration_ms=ms)


def test_assemble_broadcast_places_titled_segments_at_their_times():
    programmes = [
        Programme("Hacker News front page", Cadence(900, 360)),
        Programme("Repository activity", Cadence(900, 0.0)),
    ]
    content = {
        "Hacker News front page": _voiced("hn", 2000),
        "Repository activity": _voiced("repo", 3000),
    }
    plan = assemble_broadcast(programmes, content, window_s=1800)

    assert [s.title for s in plan.segments] == [
        "Repository activity",
        "Hacker News front page",
        "Repository activity",
        "Hacker News front page",
    ]
    assert [s.start_s for s in plan.segments] == [0.0, 360.0, 900.0, 1260.0]
    # Duration comes from each topic's audio.
    assert plan.segments[0].duration_s == 3.0
    assert plan.segments[1].duration_s == 2.0


def test_assemble_skips_programmes_without_content():
    programmes = [
        Programme("Hacker News front page", Cadence(900, 360)),
        Programme("Repository activity", Cadence(900, 0.0)),
    ]
    content = {"Repository activity": _voiced("repo", 1000)}  # HN produced nothing
    plan = assemble_broadcast(programmes, content, window_s=1800)
    assert {s.title for s in plan.segments} == {"Repository activity"}
