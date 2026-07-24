"""Tests for the serve refresh loop — offline, no web server needed."""

from __future__ import annotations

import urllib.error

from maelcom.core.models import NewsItem
from maelcom.core.schedule import Cadence
from maelcom.newsroom.tts import ToneWavTTS
from maelcom.serve import refresh_once
from maelcom.web.app import _State


class _FakeSource:
    def __init__(self, items: list):
        self._items = items

    def poll(self, since=None):
        return self._items


def _items():
    return [
        NewsItem(id="1", source="hackernews", kind="story", title="Big story",
                 origin="Hacker News", actors=["a"]),
    ]


def test_refresh_once_publishes_program_and_plan():
    state = _State()
    roster = [("Hacker News", _FakeSource(_items()), Cadence(900, 0), 5)]
    refresh_once(state, roster, ToneWavTTS(), cache={})
    assert state.program is not None
    assert "stack(" in state.program.text
    assert state.plan is not None and state.plan.segments
    assert state.plan.segments[0].title == "Hacker News"


def test_refresh_once_skips_revoicing_when_unchanged():
    state, cache = _State(), {}
    roster = [("HN", _FakeSource(_items()), Cadence(900, 0), 5)]
    refresh_once(state, roster, ToneWavTTS(), cache=cache)
    first_plan = state.plan
    refresh_once(state, roster, ToneWavTTS(), cache=cache)
    # Unchanged items → the plan object is not rebuilt (no re-voicing) ...
    assert state.plan is first_plan
    # ... but the music program is still recomputed each tick.
    assert state.program is not None


def test_refresh_once_skips_failing_sources():
    class _Bad:
        def poll(self, since=None):
            raise urllib.error.URLError("down")

    state = _State()
    roster = [
        ("HN", _FakeSource(_items()), Cadence(900, 0), 5),
        ("Bad", _Bad(), Cadence(900, 0), 5),
    ]
    refresh_once(state, roster, ToneWavTTS(), cache={})
    assert state.program is not None
    # The window airs the topic several times; only the good source appears.
    assert {s.title for s in state.plan.segments} == {"HN"}
