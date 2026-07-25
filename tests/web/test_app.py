"""Web-layer tests. Skipped unless the [web] extra (FastAPI + httpx) is present."""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

from maelcom.core.models import ActivitySignal
from maelcom.genmusic.compose import compose
from maelcom.web.app import _State, create_app


def test_health_ok():
    client = TestClient(create_app())
    assert client.get("/health").json() == {"status": "ok"}


def test_index_serves_strudel_player():
    client = TestClient(create_app())
    resp = client.get("/")
    assert resp.status_code == 200
    html = resp.text
    assert "@strudel/web" in html  # loads the Strudel runtime
    assert "initStrudel(" in html and "evaluate(" in html  # starts + plays
    assert "/genmusic" in html and "/plan" in html  # polls both endpoints
    assert 'id=\'viz\'' in html  # the visualizer canvas
    assert "/models" in html and 'id=\'model\'' in html  # the ambient-generator dropdown
    assert "/tuning" in html and 'id=\'tuning\'' in html  # the concert-A tuning dropdown


def test_genmusic_empty_then_published():
    state = _State()
    client = TestClient(create_app(state))
    # Nothing published yet.
    assert client.get("/genmusic").json() == {"text": None, "play": True}

    program = compose(
        ActivitySignal(window_s=0.0, volume=5, volatility=0.3, participant_count=2)
    )
    state.set_program(program)
    body = client.get("/genmusic").json()
    assert body["style"] == "Entrainment 0.1"
    assert body["brainwave_band"] == program.brainwave_band
    assert "stack(" in body["text"]
    assert body["fade_ms"] == 2000


def test_models_list_and_switch():
    state = _State()
    client = TestClient(create_app(state))
    listing = client.get("/models").json()
    assert listing["models"] == ["Entrainment 0.1", "ScratchPad"]
    assert listing["current"] == "Entrainment 0.1"  # default

    # Switching with a live signal recomposes immediately with the new model.
    state.last_signal = ActivitySignal(window_s=0.0, volume=5, volatility=0.3, participant_count=2)
    resp = client.post("/model", params={"name": "ScratchPad"})
    assert resp.json() == {"current": "ScratchPad"}
    assert state.model == "ScratchPad"
    assert client.get("/genmusic").json()["style"] == "ScratchPad"

    assert client.post("/model", params={"name": "Nope"}).status_code == 400


def test_models_selector_hidden_by_default():
    state = _State()
    client = TestClient(create_app(state))
    assert client.get("/models").json()["selector"] is False  # config item, off by default
    state.show_selector = True
    assert client.get("/models").json()["selector"] is True


def test_tuning_list_and_switch():
    state = _State()
    client = TestClient(create_app(state))
    listing = client.get("/tuning").json()
    assert listing["tunings"] == [440.0, 435.0, 432.0]
    assert listing["current"] == 440.0  # standard by default

    state.last_signal = ActivitySignal(window_s=0.0, volume=5, volatility=0.3, participant_count=2)
    resp = client.post("/tuning", params={"a": 432.0})
    assert resp.json() == {"current": 432.0}
    assert state.tuning == 432.0
    assert ".detune(" in client.get("/genmusic").json()["text"]  # retuned

    assert client.post("/tuning", params={"a": 441.0}).status_code == 400  # unsupported


def test_serve_refresh_makes_genmusic_and_plan_live():
    from maelcom.core.models import NewsItem
    from maelcom.core.schedule import Cadence
    from maelcom.newsroom.tts import ToneWavTTS
    from maelcom.serve import refresh_once

    class _FakeSource:
        def poll(self, since=None):
            return [NewsItem(id="1", source="hackernews", kind="story",
                             title="Story", origin="Hacker News", actors=["a"])]

    state = _State()
    client = TestClient(create_app(state))
    assert client.get("/genmusic").json() == {"text": None, "play": True}

    refresh_once(state, [("HN", _FakeSource(), Cadence(900, 0), 5)], ToneWavTTS(), cache={})
    music = client.get("/genmusic").json()
    assert music["style"] == "Entrainment 0.1" and "stack(" in music["text"]
    plan = client.get("/plan").json()
    assert plan["segments"] and plan["segments"][0]["title"] == "HN"


def test_serve_holds_the_journey_across_news_updates():
    from maelcom.core.models import NewsItem
    from maelcom.core.schedule import Cadence
    from maelcom.newsroom.tts import ToneWavTTS
    from maelcom.serve import refresh_once

    class _ChangingSource:
        def __init__(self):
            self.n = 0

        def poll(self, since=None):
            self.n += 1  # different item set each call → a different signal
            return [
                NewsItem(id=str(k), source="hackernews", kind="story",
                         title=f"S{k}", origin="Hacker News", actors=["a", "b"])
                for k in range(self.n)
            ]

    state = _State()
    cache: dict = {}
    roster = [("HN", _ChangingSource(), Cadence(900, 0), 5)]
    refresh_once(state, roster, ToneWavTTS(), cache=cache)
    first = state.program.text
    # subsequent refreshes (news/activity changing) must NOT restart the journey
    refresh_once(state, roster, ToneWavTTS(), cache=cache)
    refresh_once(state, roster, ToneWavTTS(), cache=cache)
    assert state.program.text == first  # held, not republished


def test_quiet_endpoint_toggle():
    state = _State()
    client = TestClient(create_app(state))
    assert client.get("/quiet").json() == {"quiet_mode": False, "music_on": True}
    assert client.post("/quiet", params={"on": True}).json()["quiet_mode"] is True
    assert state.quiet_mode is True and client.get("/genmusic").json()["play"] is True
    # turning quiet off resumes continuous play
    client.post("/quiet", params={"on": False})
    assert state.music_on is True


def test_quiet_mode_gates_music_around_the_news():
    from maelcom.core.models import NewsItem
    from maelcom.core.schedule import Cadence
    from maelcom.newsroom.tts import ToneWavTTS
    from maelcom.serve import refresh_once

    class _Src:
        def poll(self, since=None):
            return [NewsItem(id="1", source="hackernews", kind="story",
                             title="S", origin="HN", actors=["a"])]

    state = _State()
    state.quiet_mode = True
    cache: dict = {}
    roster = [("HN", _Src(), Cadence(900, 0), 5)]

    # t=0: fresh news → the music leads in, but the news is HELD (not aired yet)
    refresh_once(state, roster, ToneWavTTS(), cache=cache, now=0.0)
    assert state.music_on is True and state.plan is None

    # after the 1-3 min lead-in → the news airs
    refresh_once(state, roster, ToneWavTTS(), cache=cache, now=300.0)
    assert state.plan is not None

    # ~1 minute after the news → the music goes silent until the next cycle
    refresh_once(state, roster, ToneWavTTS(), cache=cache, now=400.0)
    assert state.music_on is False
