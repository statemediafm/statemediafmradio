"""Tests for the serve refresh loop — offline, no web server needed."""

from __future__ import annotations

import urllib.error

from statemediafm.core.models import NewsItem
from statemediafm.core.schedule import Cadence
from statemediafm.newsroom.llm import FakeLLMClient, LLMConfig
from statemediafm.newsroom.tts import ToneWavTTS
from statemediafm.serve import _effective_llm, _publish_plan, _voice_rotation, refresh_once
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


def _llm():
    """A fake LLM so news airs — news is always LLM-written (no offline fallback)."""
    return (FakeLLMClient(), LLMConfig(model="test/model"))


def test_effective_llm_none_without_a_model_or_client():
    state = _State()  # no news_cfg, no model, no boot client
    assert _effective_llm(state, None) is None


def test_effective_llm_uses_the_claude_cli_backend():
    from statemediafm.newsroom.llm import ClaudeCliClient

    state = _State()
    state.news_backend = "claude-cli"
    eff = _effective_llm(state, None)  # constructs the client; does NOT invoke it
    assert eff is not None
    client, _cfg = eff
    assert isinstance(client, ClaudeCliClient)  # no model needed for the CLI
    assert _effective_llm(state, None)[0] is client  # cached, not rebuilt


def test_effective_llm_builds_lazily_from_a_configured_model():
    state = _State()
    state.news_cfg = LLMConfig(model="openai/gpt-4o-mini")
    state.news_model = "openai/gpt-4o-mini"
    eff = _effective_llm(state, None)
    assert eff is not None
    client, cfg = eff
    assert cfg.model == "openai/gpt-4o-mini"
    # The lazily-built client is cached on the state (not rebuilt each tick).
    assert _effective_llm(state, None)[0] is client


def test_effective_llm_honours_a_wired_boot_client_for_backcompat():
    # A boot client (legacy --live) is used even if state.live wasn't set.
    from statemediafm.newsroom.llm import LLMConfig

    sentinel = object()
    eff = _effective_llm(_State(), (sentinel, LLMConfig(model="base/model")))
    assert eff is not None and eff[0] is sentinel and eff[1].model == "base/model"


def test_voice_rotation_leads_with_base_then_distinct_voices():
    rot = _voice_rotation("alan")
    assert rot[0] == "alan"  # the operator's chosen voice leads
    assert len(rot) == len(set(rot))  # all distinct
    assert set(rot) >= {"alan", "alba"}
    # A non-curated base still leads, curated voices behind it.
    assert _voice_rotation("en_US-lessac-medium")[0] == "en_US-lessac-medium"


def test_publish_plan_assigns_a_distinct_stable_voice_per_source():
    state = _State()
    state.voice = "alan"
    hn = NewsItem(id="h1", source="hackernews", kind="story", title="Big story",
                  origin="Hacker News", actors=["a"])
    forge = NewsItem(id="i1", source="forge", kind="issue", title="Scheduler hangs",
                     origin="app", actors=["b"])
    per_topic = [
        ("Hacker News", [hn], Cadence(900, 0), 5),
        ("Engineering", [forge], Cadence(900, 0), 5),
    ]
    cache: dict = {}
    _publish_plan(state, per_topic, ToneWavTTS(), "newsroom", 0, _llm(), cache)
    tv = cache["topic_voice"]
    # Each source gets its own voice; the first keeps the operator's base voice.
    assert tv["Hacker News"] == "alan"
    assert tv["Engineering"] != "alan"
    assert len(set(tv.values())) == 2  # distinct

    # Stable: re-publishing (even if one source is momentarily absent) keeps the map.
    _publish_plan(state, [per_topic[1]], ToneWavTTS(), "newsroom", 0, _llm(), cache)
    assert cache["topic_voice"]["Engineering"] == tv["Engineering"]


def test_refresh_once_publishes_program_and_plan():
    state = _State()
    roster = [("Hacker News", _FakeSource(_items()), Cadence(900, 0), 5)]
    cache = {}
    refresh_once(state, roster, ToneWavTTS(), cache=cache, llm=_llm())
    assert state.program is not None
    assert "stack(" in state.program.text
    assert state.plan is not None and state.plan.segments
    assert state.plan.segments[0].title == "Hacker News"
    # The latest activity is stashed so "Newscast now" can re-air without re-polling.
    assert cache.get("last_per_topic") and cache["last_per_topic"][0][0] == "Hacker News"


def test_plan_carries_headline_links_for_the_page_list():
    state = _State()
    items = [
        NewsItem(id="1", source="hackernews", kind="story", title="Big story",
                 origin="Hacker News", actors=["a"], refs=["https://news.ycombinator.com/item?id=1"]),
        NewsItem(id="2", source="hackernews", kind="story", title="No link here",
                 origin="Hacker News", actors=["b"]),
    ]
    roster = [("HN", _FakeSource(items), Cadence(900, 0), 5)]
    refresh_once(state, roster, ToneWavTTS(), cache={}, llm=_llm())
    heads = state.plan.segments[0].headlines
    assert ("Big story", "https://news.ycombinator.com/item?id=1") in heads
    assert ("No link here", None) in heads  # no ref → plain text, no crash


def test_refresh_once_skips_revoicing_when_unchanged():
    state, cache = _State(), {}
    roster = [("HN", _FakeSource(_items()), Cadence(900, 0), 5)]
    refresh_once(state, roster, ToneWavTTS(), cache=cache, llm=_llm())
    first_plan = state.plan
    refresh_once(state, roster, ToneWavTTS(), cache=cache, llm=_llm())
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
    refresh_once(state, roster, ToneWavTTS(), cache={}, llm=_llm())
    assert state.plan.segments[0].script.style == "sports-desk"


def test_refresh_once_airs_no_news_when_the_llm_fails():
    class _Boom(FakeLLMClient):
        def complete(self, prompt, cfg):
            raise RuntimeError("gateway down")

    state = _State()
    roster = [("HN", _FakeSource(_items()), Cadence(900, 0), 5)]
    refresh_once(state, roster, ToneWavTTS(), cache={}, llm=(_Boom(), LLMConfig(model="m")))
    # No deterministic fallback: the bulletin is skipped, but the music still plays.
    assert state.plan is None
    assert state.program is not None


def test_refresh_once_airs_no_news_without_a_model():
    # No LLM configured at all → no bulletin (music only).
    state = _State()
    roster = [("HN", _FakeSource(_items()), Cadence(900, 0), 5)]
    refresh_once(state, roster, ToneWavTTS(), cache={})  # llm=None
    assert state.plan is None and state.program is not None


def test_refresh_once_gates_news_to_director_windows():
    from statemediafm.core.director import Director

    state = _State()
    director = Director()  # 17-min news cadence
    roster = [("HN", _FakeSource(_items()), Cadence(900, 0), 5)]
    cache: dict = {}

    # First tick (elapsed 0): the opening bulletin airs.
    refresh_once(state, roster, ToneWavTTS(), cache=cache, director=director, now=1000.0, llm=_llm())
    assert state.plan is not None
    first = state.plan

    # A minute later, fresh activity but no news slot due → the plan is held.
    roster[0] = ("HN", _FakeSource([
        NewsItem(id="9", source="hackernews", kind="story", title="New drop",
                 origin="Hacker News", actors=["z"])]), Cadence(900, 0), 5)
    refresh_once(state, roster, ToneWavTTS(), cache=cache, director=director, now=1060.0, llm=_llm())
    assert state.plan is first  # not re-aired between windows

    # Past the 17-min slot → the held fresh news airs.
    refresh_once(state, roster, ToneWavTTS(), cache=cache, director=director,
                 now=1000.0 + 17 * 60 + 1, llm=_llm())
    assert state.plan is not first


def test_mix_mode_rotates_the_ambient_generator():
    state = _State()
    state.mix_generators = True
    state.mix_models = ["Entrainment 0.1", "Space Dub"]
    roster = [("HN", _FakeSource(_items()), Cadence(900, 0), 5)]
    cache: dict = {}
    from statemediafm.serve import MIX_EVERY_S

    state.model = "ScratchPad"  # the user's OWN selection — rotation must not clobber it
    # First tick → first generator in the pool.
    refresh_once(state, roster, ToneWavTTS(), cache=cache, now=1000.0)
    assert state.program.style == "Entrainment 0.1"
    # One MIX window later → the next generator (the bed changes).
    refresh_once(state, roster, ToneWavTTS(), cache=cache, now=1000.0 + MIX_EVERY_S + 1)
    assert state.program.style == "Space Dub"
    assert state.model == "ScratchPad"  # rotation left the selection untouched
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
    refresh_once(state, roster, ToneWavTTS(), cache={}, llm=_llm())
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
    refresh_once(state, roster, ToneWavTTS(), cache={}, llm=_llm())
    assert state.program is not None
    # The window airs the topic several times; only the good source appears.
    assert {s.title for s in state.plan.segments} == {"HN"}
