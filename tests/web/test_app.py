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
    assert "/auth" in html and "data-tab='settings'" in html  # the Settings tab
    assert "/broadcast" in html and 'id=\'stopbtn\'' in html  # the stop-broadcast control


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


def test_news_model_not_live_by_default():
    client = TestClient(create_app(_State()))
    d = client.get("/news-model").json()
    assert d["live"] is False and d["current"] is None
    # Selecting a model is rejected when news parsing isn't live.
    assert client.post("/news-model", params={"name": "openai/x"}).status_code == 409


def test_news_model_select_when_live():
    state = _State()
    state.news_model = "anthropic/claude-opus-4-8"  # seeded live by serve.run
    state.news_models = ["anthropic/claude-opus-4-8", "openai/gpt-4o-mini"]
    client = TestClient(create_app(state))

    d = client.get("/news-model").json()
    assert d["live"] is True and d["current"] == "anthropic/claude-opus-4-8"

    resp = client.post("/news-model", params={"name": "openai/gpt-4o-mini"})
    assert resp.json()["current"] == "openai/gpt-4o-mini"
    assert state.news_model == "openai/gpt-4o-mini"

    # A custom model is accepted and remembered in the options list.
    resp = client.post("/news-model", params={"name": "ollama/llama3.1"})
    assert state.news_model == "ollama/llama3.1"
    assert "ollama/llama3.1" in resp.json()["models"]
    assert client.post("/news-model", params={"name": "  "}).status_code == 400


def test_news_model_discovery_merges_gateway_models(monkeypatch):
    import maelcom.newsroom.llm as llm_pkg
    from maelcom.newsroom.llm import LLMConfig

    # The endpoint imports discover_models from the package namespace — patch there.
    monkeypatch.setattr(llm_pkg, "discover_models",
                        lambda cfg, **kw: ["openai/gpt-4o-mini", "openai/o1"])

    state = _State()
    state.news_model = "openai/gpt-4o-mini"
    state.news_models = ["openai/gpt-4o-mini"]
    state.news_cfg = LLMConfig(model="openai/gpt-4o-mini", api_base="https://gw/v1")
    client = TestClient(create_app(state))

    d = client.post("/news-model/discover").json()
    assert d["discovered"] == ["openai/gpt-4o-mini", "openai/o1"]
    # New model merged in without duplicating the one already listed.
    assert state.news_models == ["openai/gpt-4o-mini", "openai/o1"]


def test_news_model_discovery_rejected_when_not_live():
    client = TestClient(create_app(_State()))
    assert client.post("/news-model/discover").status_code == 409


def test_sources_list_add_and_remove():
    from maelcom.roster import build_segment

    state = _State()
    state.segments = [{"topic": "HN", "source": "hackernews"}]
    state.roster = [build_segment(state.segments[0])]
    client = TestClient(create_app(state))

    listing = client.get("/sources").json()
    assert listing["sources"][0]["kind"] == "hackernews"
    assert "slack" in listing["kinds"] and "jira" in listing["kinds"]

    # Add a source live → it lands in the live roster the refresh loop reads.
    resp = client.post("/sources", json={"source": "slack", "channel": "general", "topic": "Chat"})
    assert resp.status_code == 200 and resp.json()["topic"] == "Chat"
    assert len(state.roster) == 2 and state.segments[1]["channel"] == "general"

    # A kind that needs a param but is missing it → 400, roster unchanged.
    assert client.post("/sources", json={"source": "jira"}).status_code == 400
    assert len(state.roster) == 2

    # Remove by index.
    assert client.delete("/sources/0").json()["removed"] == 0
    assert len(state.roster) == 1 and state.roster[0][0] == "Chat"
    assert client.delete("/sources/9").status_code == 404


def test_sources_never_leak_tokens():
    from maelcom.roster import build_segment

    state = _State()
    state.segments = [{"topic": "R", "source": "repo",
                       "repo": "https://github.com/x/y", "token": "ghp_secret"}]
    state.roster = [build_segment(state.segments[0])]
    client = TestClient(create_app(state))
    import json as _json
    assert "ghp_secret" not in _json.dumps(client.get("/sources").json())


def test_sources_add_honours_headlines_and_max_count():
    state = _State()
    state.segments, state.roster = [], []
    client = TestClient(create_app(state))
    resp = client.post("/sources", json={"source": "hackernews", "topic": "HN",
                                         "headlines": 3, "max_count": 7, "offset": "5m"})
    assert resp.status_code == 200
    assert state.roster[0][3] == 3  # headlines cap flows into the roster entry
    listed = client.get("/sources").json()["sources"][0]["config"]
    assert listed["headlines"] == 3 and listed["max_count"] == 7


def test_style_and_voice_endpoints():
    state = _State()
    client = TestClient(create_app(state))
    assert "bbc-world" in client.get("/style").json()["suggestions"]
    assert client.post("/style", params={"name": "noir"}).json()["current"] == "noir"
    assert state.style == "noir"
    assert client.post("/style", params={"name": "  "}).status_code == 400

    voices = client.get("/voice").json()["voices"]
    assert "alan" in voices and "alba" in voices
    assert client.post("/voice", params={"name": "alba"}).json()["current"] == "alba"
    assert state.voice == "alba"
    assert client.post("/voice", params={"name": "nope"}).status_code == 400


def test_news_model_temperature_and_max_tokens():
    state = _State()
    state.news_model = "openai/gpt-4o-mini"
    client = TestClient(create_app(state))
    resp = client.post("/news-model", params={"name": "openai/o1", "temperature": 0.3,
                                              "max_tokens": 512})
    assert resp.json()["temperature"] == 0.3 and resp.json()["max_tokens"] == 512
    assert state.news_temperature == 0.3 and state.news_max_tokens == 512
    # Out-of-range values are rejected.
    assert client.post("/news-model", params={"name": "m", "temperature": 5}).status_code == 400
    assert client.post("/news-model", params={"name": "m", "max_tokens": 0}).status_code == 400


def test_llm_presets_listed():
    client = TestClient(create_app(_State()))
    names = [p["name"] for p in client.get("/llm-presets").json()["presets"]]
    assert "OpenRouter" in names and "Ollama" in names


def test_auth_endpoints_store_and_mask_tokens(monkeypatch, tmp_path):
    import json as _json

    monkeypatch.setenv("MAELCOM_AUTH", str(tmp_path / "auth.toml"))
    client = TestClient(create_app())

    got = client.get("/auth").json()
    assert "github" in got["sources"] and got["config"]["github"]["token_set"] is False

    # The token is sent in the body (never the URL) and stored gitignored.
    resp = client.post("/auth", json={"source": "github", "token": "ghp_supersecret9999"})
    cfg = resp.json()["config"]
    assert cfg["github"]["token_set"] is True and cfg["github"]["token_hint"].endswith("9999")

    # The raw token is never returned by the API.
    assert "ghp_supersecret9999" not in _json.dumps(client.get("/auth").json())
    assert client.post("/auth", json={"source": "nope", "token": "x"}).status_code == 400


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


def test_stop_broadcast_pauses_the_loop_and_silences():
    from maelcom.core.models import NewsItem
    from maelcom.core.schedule import Cadence
    from maelcom.newsroom.tts import ToneWavTTS
    from maelcom.serve import refresh_once

    polled = {"n": 0}

    class _Src:
        def poll(self, since=None):
            polled["n"] += 1
            return [NewsItem(id="1", source="hackernews", kind="story",
                             title="S", origin="HN", actors=["a"])]

    state = _State()
    client = TestClient(create_app(state))
    roster = [("HN", _Src(), Cadence(900, 0), 5)]

    # Stop → the endpoint flips state and silences; a refresh does NO work.
    assert client.post("/broadcast", params={"on": False}).json() == {"broadcasting": False}
    assert state.broadcasting is False and state.music_on is False
    assert client.get("/genmusic").json()["play"] is False
    refresh_once(state, roster, ToneWavTTS(), cache={})
    assert polled["n"] == 0  # no polling / TTS while stopped

    # Resume → work happens again and audio is restored.
    assert client.post("/broadcast", params={"on": True}).json() == {"broadcasting": True}
    assert state.music_on is True
    refresh_once(state, roster, ToneWavTTS(), cache={})
    assert polled["n"] == 1


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
