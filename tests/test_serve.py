"""Tests for the serve refresh loop — offline, no web server needed."""

from __future__ import annotations

import urllib.error

from statemediafm.core.models import NewsItem
from statemediafm.core.schedule import Cadence
from statemediafm.newsroom.tts import ToneWavTTS
from statemediafm.serve import refresh_once
from statemediafm.web.app import _State


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


def test_plan_carries_headline_links_for_the_page_list():
    state = _State()
    items = [
        NewsItem(id="1", source="hackernews", kind="story", title="Big story",
                 origin="Hacker News", actors=["a"], refs=["https://news.ycombinator.com/item?id=1"]),
        NewsItem(id="2", source="hackernews", kind="story", title="No link here",
                 origin="Hacker News", actors=["b"]),
    ]
    roster = [("HN", _FakeSource(items), Cadence(900, 0), 5)]
    refresh_once(state, roster, ToneWavTTS(), cache={})
    heads = state.plan.segments[0].headlines
    assert ("Big story", "https://news.ycombinator.com/item?id=1") in heads
    assert ("No link here", None) in heads  # no ref → plain text, no crash


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


def test_refresh_once_uses_llm_when_wired():
    from statemediafm.newsroom.llm import FakeLLMClient, LLMConfig

    state = _State()
    state.news_model = "openai/gpt-4o-mini"  # UI selection overrides the base model
    roster = [("HN", _FakeSource(_items()), Cadence(900, 0), 5)]
    seen = {}

    class _Rec(FakeLLMClient):
        def complete(self, prompt, cfg):
            seen["model"] = cfg.model
            return "The team shipped a big story today."

    refresh_once(state, roster, ToneWavTTS(), cache={}, llm=(_Rec(), LLMConfig(model="base/model")))
    # The live model wrote the segment, using the UI-selected model, not the base.
    assert seen["model"] == "openai/gpt-4o-mini"
    assert "The team shipped a big story today." in state.plan.segments[0].script.text


def test_refresh_once_applies_live_llm_overrides():
    from statemediafm.newsroom.llm import FakeLLMClient, LLMConfig

    state = _State()
    state.news_model = "openai/o1"
    state.news_temperature = 0.2
    state.news_max_tokens = 256
    seen = {}

    class _Rec(FakeLLMClient):
        def complete(self, prompt, cfg):
            seen.update(model=cfg.model, temp=cfg.temperature, mx=cfg.max_tokens)
            return "Body."

    base = LLMConfig(model="base/model", temperature=1.0, max_tokens=1024)
    roster = [("HN", _FakeSource(_items()), Cadence(900, 0), 5)]
    refresh_once(state, roster, ToneWavTTS(), cache={}, llm=(_Rec(), base))
    assert seen == {"model": "openai/o1", "temp": 0.2, "mx": 256}


def test_refresh_once_uses_live_style():
    state = _State()
    state.style = "sports-desk"
    roster = [("HN", _FakeSource(_items()), Cadence(900, 0), 5)]
    refresh_once(state, roster, ToneWavTTS(), cache={})
    assert state.plan.segments[0].script.style == "sports-desk"


def test_refresh_once_falls_back_when_llm_errors():
    from statemediafm.newsroom.llm import FakeLLMClient, LLMConfig

    class _Boom(FakeLLMClient):
        def complete(self, prompt, cfg):
            raise RuntimeError("gateway down")

    state = _State()
    roster = [("HN", _FakeSource(_items()), Cadence(900, 0), 5)]
    refresh_once(state, roster, ToneWavTTS(), cache={}, llm=(_Boom(), LLMConfig(model="m")))
    # A live-model failure degrades to the deterministic copy — still on air.
    assert state.plan is not None and state.plan.segments
    assert "firmwide radio service" in state.plan.segments[0].script.text


def test_refresh_once_gates_news_to_director_windows():
    from statemediafm.core.director import Director

    state = _State()
    director = Director()  # 17-min news cadence
    roster = [("HN", _FakeSource(_items()), Cadence(900, 0), 5)]
    cache: dict = {}

    # First tick (elapsed 0): the opening bulletin airs.
    refresh_once(state, roster, ToneWavTTS(), cache=cache, director=director, now=1000.0)
    assert state.plan is not None
    first = state.plan

    # A minute later, fresh activity but no news slot due → the plan is held.
    roster[0] = ("HN", _FakeSource([
        NewsItem(id="9", source="hackernews", kind="story", title="New drop",
                 origin="Hacker News", actors=["z"])]), Cadence(900, 0), 5)
    refresh_once(state, roster, ToneWavTTS(), cache=cache, director=director, now=1060.0)
    assert state.plan is first  # not re-aired between windows

    # Past the 17-min slot → the held fresh news airs.
    refresh_once(state, roster, ToneWavTTS(), cache=cache, director=director, now=1000.0 + 17 * 60 + 1)
    assert state.plan is not first


def test_mix_mode_rotates_the_ambient_generator():
    state = _State()
    state.mix_generators = True
    state.mix_models = ["Entrainment 0.1", "Space Dub"]
    roster = [("HN", _FakeSource(_items()), Cadence(900, 0), 5)]
    cache: dict = {}
    from statemediafm.serve import MIX_EVERY_S

    # First tick → first generator in the pool.
    refresh_once(state, roster, ToneWavTTS(), cache=cache, now=1000.0)
    assert state.program.style == "Entrainment 0.1"
    # One MIX window later → the next generator (the bed changes).
    refresh_once(state, roster, ToneWavTTS(), cache=cache, now=1000.0 + MIX_EVERY_S + 1)
    assert state.program.style == "Space Dub"
    # Off (single generator) holds the chosen model.
    state.mix_generators = False
    state.model = "Entrainment 0.1"
    s2, cache2 = _State(), {}
    s2.model = "Space Dub"
    refresh_once(s2, roster, ToneWavTTS(), cache=cache2, now=1.0)
    first = s2.program
    refresh_once(s2, roster, ToneWavTTS(), cache=cache2, now=999.0)
    assert s2.program is first  # not mixing → held, no rotation


def test_refresh_once_reads_a_live_edited_roster():
    # The Settings tab appends to state.roster mid-session; the loop reads it live.
    state = _State()
    roster = [("HN", _FakeSource(_items()), Cadence(900, 0), 5)]
    extra = NewsItem(id="2", source="slack", kind="message", title="Deploy done",
                     origin="Slack", actors=["b"])
    roster.append(("Chat", _FakeSource([extra]), Cadence(900, 0), 5))
    refresh_once(state, roster, ToneWavTTS(), cache={})
    assert {s.title for s in state.plan.segments} == {"HN", "Chat"}


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
