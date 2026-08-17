"""Web-layer tests. Skipped unless the [web] extra (FastAPI + httpx) is present."""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

from statemediafm.core.models import ActivitySignal
from statemediafm.genmusic.compose import compose
from statemediafm.web.app import _State, create_app


def test_health_ok():
    client = TestClient(create_app())
    assert client.get("/health").json() == {"status": "ok"}


def test_news_now_calls_the_air_hook():
    state = _State()
    calls = []
    state.air_news_now = lambda: (calls.append(1), True)[1]
    client = TestClient(create_app(state))
    assert client.post("/news-now").json() == {"aired": True}
    assert calls  # the hook ran

    # No activity yet → aired False, no crash.
    state.air_news_now = lambda: False
    assert client.post("/news-now").json() == {"aired": False}


def test_news_now_no_hook_is_safe():
    client = TestClient(create_app(_State()))  # no hook wired (e.g. embedder/tests)
    assert client.post("/news-now").json() == {"aired": False}


def test_next_news_countdown():
    import time

    from statemediafm.core.director import Director
    from statemediafm.core.schedule import Cadence

    state = _State()
    state.director = Director(news=Cadence(600))  # a bulletin every 10 min
    state.session_t0 = time.monotonic() - 60  # 60 s into the session
    d = TestClient(create_app(state)).get("/next-news").json()
    assert d["every_s"] == 600
    # next slot is at 600 s; ~540 s remain
    assert 530 <= d["in_s"] <= 600


def test_next_news_none_without_a_director():
    d = TestClient(create_app(_State())).get("/next-news").json()
    assert d["in_s"] is None


def test_cadence_get_set_and_live_retime():
    from statemediafm.core.director import Director

    state = _State()
    state.director = Director()
    client = TestClient(create_app(state))

    d = client.get("/cadence").json()
    assert d["refresh_s"] == 60.0

    # A duration string re-times the live Director and the poll interval.
    r = client.post("/cadence", params={"news_every": "5m", "refresh": "30s"}).json()
    assert r["news_every_s"] == 300.0 and r["refresh_s"] == 30.0
    assert state.news_every_s == 300.0
    assert state.director.news.every_s == 300.0  # the running rhythm changed
    assert state.refresh_s == 30.0

    # Bare seconds and validation.
    assert client.post("/cadence", params={"refresh": "90"}).json()["refresh_s"] == 90.0
    assert client.post("/cadence", params={"news_every": "0"}).status_code == 400
    assert client.post("/cadence", params={"refresh": "0.5"}).status_code == 400
    assert client.post("/cadence", params={"news_every": "nonsense"}).status_code == 400


def test_mutation_fires_persist_hook_but_reads_do_not():
    state = _State()
    calls = []
    state.on_change = lambda: calls.append(1)
    client = TestClient(create_app(state))

    client.post("/intensity", params={"level": 0.5})
    assert calls  # a successful settings mutation persisted

    calls.clear()
    client.get("/genmusic")
    assert not calls  # a read does not persist


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
    assert "/broadcast" in html and 'id=\'play\'' in html  # the transport (start/pause) control


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
    assert listing["models"][:2] == ["Entrainment 0.1", "ScratchPad"]
    assert {"Space Dub", "Modular Bleep"} <= set(listing["models"])
    assert listing["current"] == "Entrainment 0.1"  # default

    # Switching with a live signal recomposes immediately with the new model.
    state.last_signal = ActivitySignal(window_s=0.0, volume=5, volatility=0.3, participant_count=2)
    resp = client.post("/model", params={"name": "ScratchPad"})
    assert resp.json() == {"current": "ScratchPad"}
    assert state.model == "ScratchPad"
    assert client.get("/genmusic").json()["style"] == "ScratchPad"

    assert client.post("/model", params={"name": "Nope"}).status_code == 400


def test_news_backend_get_and_set():
    state = _State()
    client = TestClient(create_app(state))
    d = client.get("/news-backend").json()
    assert d["backend"] == "gateway" and set(d["options"]) == {"claude-cli", "gateway"}
    assert "claude_available" in d

    r = client.post("/news-backend", params={"backend": "claude-cli"})
    assert r.json()["backend"] == "claude-cli" and state.news_backend == "claude-cli"
    assert client.post("/news-backend", params={"backend": "nope"}).status_code == 400


def test_sources_list_add_and_remove():
    from statemediafm.roster import build_segment

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
    from statemediafm.roster import build_segment

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


def test_voice_endpoint():
    state = _State()
    client = TestClient(create_app(state))
    voices = client.get("/voice").json()["voices"]
    assert "alan" in voices and "alba" in voices
    assert client.post("/voice", params={"name": "alba"}).json()["current"] == "alba"
    assert state.voice == "alba"
    assert client.post("/voice", params={"name": "nope"}).status_code == 400


def test_personas_are_locked_without_a_license(monkeypatch):
    monkeypatch.delenv("STATEMEDIAFM_LICENSE", raising=False)
    monkeypatch.setenv("STATEMEDIAFM_LICENSE_FILE", "/nonexistent/statemediafm.license")
    state = _State()
    client = TestClient(create_app(state))

    d = client.get("/persona").json()
    assert d["current"] == "Custom" and d["licensed"] is False
    assert "Newsroom" in d["personas"]  # listed, but locked

    # Selecting a persona without the license is rejected (402), Custom is free.
    assert client.post("/persona", params={"name": "Newsroom"}).status_code == 402
    assert state.persona is None
    assert client.post("/persona", params={"name": "Custom"}).status_code == 200


def test_intensity_endpoint_sets_base_energy():
    from statemediafm.core.models import ActivitySignal

    state = _State()
    client = TestClient(create_app(state))
    assert client.get("/intensity").json() == {"current": 0.25, "band": "theta"}

    resp = client.post("/intensity", params={"level": 0.9}).json()
    assert resp["current"] == 0.9 and resp["band"] == "gamma"
    assert state.base_intensity == 0.9
    assert client.post("/intensity", params={"level": 2}).status_code == 400

    # With a signal present, changing base energy recomposes immediately.
    state.last_signal = ActivitySignal(window_s=0.0, volume=0, volatility=0.0,
                                       participant_count=0)
    client.post("/intensity", params={"level": 0.05})
    assert state.program is not None and state.program.brainwave_band == "delta"


def test_persona_selection_sets_style_voice_and_phrasing(monkeypatch):
    import statemediafm.licensing as lic
    from statemediafm.newsroom.personas import MODULE

    # Verification is stubbed, so simulate a future valid (asymmetric) license that
    # unlocks the module — exercises the licensed persona-selection path.
    monkeypatch.setattr(lic, "_verify", lambda key, now=None: frozenset([MODULE]))
    monkeypatch.setenv("STATEMEDIAFM_LICENSE", "valid-key")
    state = _State()
    client = TestClient(create_app(state))

    assert client.get("/persona").json()["licensed"] is True
    resp = client.post("/persona", params={"name": "Newsroom"}).json()
    assert resp["current"] == "Newsroom"
    assert state.persona == "Newsroom"
    assert state.style == "newsroom" and state.voice == "alan"
    assert state.ident and state.signoff  # station phrasing set

    # Custom clears the persona and its phrasing (back to defaults).
    client.post("/persona", params={"name": "Custom"})
    assert state.persona is None and state.ident is None and state.signoff is None
    assert state.style == "newsroom"  # keeps the last style/voice

    assert client.post("/persona", params={"name": "Nope"}).status_code == 400


def test_license_endpoint_saves_key_but_unlocks_nothing_while_stubbed(monkeypatch, tmp_path):
    monkeypatch.delenv("STATEMEDIAFM_LICENSE", raising=False)
    monkeypatch.setenv("STATEMEDIAFM_LICENSE_FILE", str(tmp_path / "statemediafm.license"))
    client = TestClient(create_app(_State()))

    assert client.get("/license").json()["has_key"] is False
    # A key is stored (has_key flips), but verification is stubbed so it unlocks
    # nothing and personas stay locked — the safe default until asymmetric signing.
    d = client.post("/license", json={"key": "a-key-the-user-pasted"}).json()
    assert client.get("/license").json()["has_key"] is True
    assert all(not m["entitled"] for m in d["modules"])
    assert client.get("/persona").json()["licensed"] is False


def test_mix_settings_roundtrip():
    state = _State()
    client = TestClient(create_app(state))
    d = client.get("/mix").json()
    assert d["mix_generators"] is False and "Space Dub" in d["models"]
    assert d["selected"] == d["models"]  # empty selection → all generators

    got = client.post("/mix", json={"mix_generators": True, "mix_spotify": True,
                                    "selected": ["Space Dub", "Entrainment 0.1", "bogus"]}).json()
    assert got["mix_generators"] is True and got["mix_spotify"] is True
    assert got["selected"] == ["Space Dub", "Entrainment 0.1"]  # unknown dropped
    assert state.mix_generators is True and state.mix_models == ["Space Dub", "Entrainment 0.1"]


def test_spotify_oauth_endpoints(tmp_path, monkeypatch):
    monkeypatch.setenv("STATEMEDIAFM_AUTH", str(tmp_path / "auth.toml"))
    state = _State()
    client = TestClient(create_app(state))

    # Not connected yet.
    assert client.get("/spotify/me").json() == {"connected": False}
    assert client.get("/spotify/token").status_code == 401
    assert client.get("/spotify/playlists").status_code == 401

    # Login needs credentials first.
    client.post("/spotify", json={"client_id": "CID", "client_secret": "SEC"})
    resp = client.get("/spotify/login", follow_redirects=False)
    assert resp.status_code in (302, 307)
    loc = resp.headers["location"]
    assert "accounts.spotify.com/authorize" in loc and "client_id=CID" in loc
    assert state.sp_oauth_state and state.sp_oauth_state in loc  # CSRF state carried

    # Callback with a bad state is rejected (→ redirect to an error, no tokens).
    bad = client.get("/spotify/callback", params={"code": "x", "state": "wrong"},
                     follow_redirects=False)
    assert bad.status_code in (302, 307) and "spotify=state" in bad.headers["location"]
    assert state.sp_access_token is None

    # A logged-in session (set directly) surfaces via /me and clears on logout.
    import time as _t
    state.sp_access_token = "A"
    state.sp_expires_at = _t.time() + 3600
    state.sp_user = {"id": "u", "name": "Jamie", "premium": True}
    assert client.get("/spotify/me").json() == {"connected": True, "id": "u", "name": "Jamie", "premium": True}
    assert client.get("/spotify/token").json()["access_token"] == "A"
    client.post("/spotify/logout")
    assert client.get("/spotify/me").json() == {"connected": False}


def test_song_endpoint_and_immediate_publish_on_mix_toggle():
    state = _State()
    client = TestClient(create_app(state))
    assert client.get("/song").json() == {}  # nothing playing

    # Turning on "mix Spotify" surfaces a song right away (unresolved without creds).
    client.post("/mix", json={"mix_spotify": True})
    d = client.get("/song").json()
    assert d.get("title") and d.get("artist")  # named from the playlist
    # Turning it off clears the song slot.
    client.post("/mix", json={"mix_spotify": False})
    assert client.get("/song").json() == {}


def test_spotify_credentials_saved_and_masked(tmp_path, monkeypatch):
    import json as _json

    monkeypatch.setenv("STATEMEDIAFM_AUTH", str(tmp_path / "auth.toml"))
    client = TestClient(create_app())

    assert client.get("/spotify").json() == {"client_id": "", "secret_set": False, "configured": False}
    got = client.post("/spotify", json={"client_id": "abc123", "client_secret": "sh_supersecret"}).json()
    assert got == {"client_id": "abc123", "secret_set": True, "configured": True}
    # The Client ID is shown; the secret is never returned by the API.
    assert "sh_supersecret" not in _json.dumps(client.get("/spotify").json())
    # Saving just the id again keeps the stored secret (blank secret doesn't wipe it).
    client.post("/spotify", json={"client_id": "abc123", "client_secret": ""})
    assert client.get("/spotify").json()["configured"] is True


def test_llm_presets_listed():
    client = TestClient(create_app(_State()))
    names = [p["name"] for p in client.get("/llm-presets").json()["presets"]]
    assert "LiteLLM" in names and "OpenRouter" in names and "Ollama" in names


def test_auth_endpoints_store_and_mask_tokens(monkeypatch, tmp_path):
    import json as _json

    monkeypatch.setenv("STATEMEDIAFM_AUTH", str(tmp_path / "auth.toml"))
    client = TestClient(create_app())

    got = client.get("/auth").json()
    assert "github" in got["sources"] and got["config"]["github"]["token_set"] is False
    # Gateways are configured separately from the news sources.
    assert "llm-gateway" in got["gateways"] and "llm-gateway" not in got["sources"]

    # The token is sent in the body (never the URL) and stored gitignored.
    resp = client.post("/auth", json={"source": "github", "token": "ghp_supersecret9999"})
    cfg = resp.json()["config"]
    assert cfg["github"]["token_set"] is True and cfg["github"]["token_hint"].endswith("9999")

    # The raw token is never returned by the API.
    assert "ghp_supersecret9999" not in _json.dumps(client.get("/auth").json())
    assert client.post("/auth", json={"source": "nope", "token": "x"}).status_code == 400


def test_models_selector_shown_by_default_and_toggleable():
    state = _State()
    client = TestClient(create_app(state))
    assert client.get("/models").json()["selector"] is True  # config item, on by default
    state.show_selector = False
    assert client.get("/models").json()["selector"] is False


def test_tuning_list_and_switch():
    state = _State()
    client = TestClient(create_app(state))
    listing = client.get("/tuning").json()
    assert listing["tunings"] == [440.0, 435.0, 433.0]
    assert listing["current"] == 433.0  # the station's default tuning

    state.last_signal = ActivitySignal(window_s=0.0, volume=5, volatility=0.3, participant_count=2)
    resp = client.post("/tuning", params={"a": 433.0})
    assert resp.json() == {"current": 433.0}
    assert state.tuning == 433.0
    assert ".detune(" in client.get("/genmusic").json()["text"]  # retuned

    assert client.post("/tuning", params={"a": 441.0}).status_code == 400  # unsupported


def test_serve_refresh_makes_genmusic_and_plan_live():
    from statemediafm.core.models import NewsItem
    from statemediafm.core.schedule import Cadence
    from statemediafm.newsroom.llm import FakeLLMClient, LLMConfig
    from statemediafm.newsroom.tts import ToneWavTTS
    from statemediafm.serve import refresh_once

    class _FakeSource:
        def poll(self, since=None):
            return [NewsItem(id="1", source="hackernews", kind="story",
                             title="Story", origin="Hacker News", actors=["a"])]

    state = _State()
    client = TestClient(create_app(state))
    assert client.get("/genmusic").json() == {"text": None, "play": True}

    refresh_once(state, [("HN", _FakeSource(), Cadence(900, 0), 5)], ToneWavTTS(), cache={},
                 llm=(FakeLLMClient(), LLMConfig(model="m")))
    music = client.get("/genmusic").json()
    assert music["style"] == "Entrainment 0.1" and "stack(" in music["text"]
    plan = client.get("/plan").json()
    assert plan["segments"] and plan["segments"][0]["title"] == "HN"


def test_serve_holds_the_journey_across_news_updates():
    from statemediafm.core.models import NewsItem
    from statemediafm.core.schedule import Cadence
    from statemediafm.newsroom.tts import ToneWavTTS
    from statemediafm.serve import refresh_once

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
    from statemediafm.core.models import NewsItem
    from statemediafm.core.schedule import Cadence
    from statemediafm.newsroom.tts import ToneWavTTS
    from statemediafm.serve import refresh_once

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


def test_index_is_not_cached():
    # no-store so a running session always gets the latest UI (Demo Mode etc.).
    resp = TestClient(create_app()).get("/")
    assert "no-store" in resp.headers.get("cache-control", "")


def test_index_has_demo_mode_toggle():
    html = TestClient(create_app()).get("/").text
    assert "Demo Mode" in html and "id='demo'" in html  # the Settings toggle


def test_demo_mode_adds_and_removes_sources():
    state = _State()
    client = TestClient(create_app(state))
    assert client.get("/demo").json() == {"demo_mode": False}

    # On: adds Hacker News + a repo (git issues), and switches quiet mode off.
    state.quiet_mode = True
    body = client.post("/demo", params={"on": True}).json()
    assert body == {"demo_mode": True}
    assert state.demo_mode is True and state.quiet_mode is False
    topics = {s["topic"] for s in state.segments}
    assert {"Hacker News front page", "Engineering issues"} <= topics
    assert len(state.roster) == len(state.segments)  # roster/segments stay in sync

    # Off: removes exactly the two sources it added.
    client.post("/demo", params={"on": False})
    assert state.demo_mode is False
    topics = {s["topic"] for s in state.segments}
    assert "Hacker News front page" not in topics and "Engineering issues" not in topics
    assert len(state.roster) == len(state.segments)


def test_demo_mode_re_reads_every_two_minutes_even_when_unchanged():
    from statemediafm.core.models import NewsItem
    from statemediafm.core.schedule import Cadence
    from statemediafm.newsroom.llm import FakeLLMClient, LLMConfig
    from statemediafm.newsroom.tts import ToneWavTTS
    from statemediafm.serve import refresh_once

    llm = (FakeLLMClient(), LLMConfig(model="m"))
    state = _State()
    state.demo_mode = True  # no director → demo's 2-min cadence, re-read regardless
    item = NewsItem(id="1", source="hackernews", kind="story", title="Big",
                    origin="Hacker News", actors=["a"])

    class _Src:  # the SAME items every poll (front page unchanged)
        def poll(self, since=None):
            return [item]

    roster = [("HN", _Src(), Cadence(900, 0), 5)]
    cache: dict = {}
    # Opening bulletin airs on the first tick.
    refresh_once(state, roster, ToneWavTTS(), cache=cache, now=1000.0, llm=llm)
    first = state.plan
    assert first is not None
    # 1 minute later the 2-min slot isn't due → held.
    refresh_once(state, roster, ToneWavTTS(), cache=cache, now=1060.0, llm=llm)
    assert state.plan is first
    # Past the 2-min slot → RE-READS even though the items are identical (the demo
    # shows the rhythm; normal mode would hold on unchanged activity).
    refresh_once(state, roster, ToneWavTTS(), cache=cache, now=1000.0 + 120 + 1, llm=llm)
    assert state.plan is not first


def test_quiet_mode_gates_music_around_the_news():
    from statemediafm.core.models import NewsItem
    from statemediafm.core.schedule import Cadence
    from statemediafm.newsroom.llm import FakeLLMClient, LLMConfig
    from statemediafm.newsroom.tts import ToneWavTTS
    from statemediafm.serve import refresh_once

    llm = (FakeLLMClient(), LLMConfig(model="m"))

    class _Src:
        def poll(self, since=None):
            return [NewsItem(id="1", source="hackernews", kind="story",
                             title="S", origin="HN", actors=["a"])]

    state = _State()
    state.quiet_mode = True
    cache: dict = {}
    roster = [("HN", _Src(), Cadence(900, 0), 5)]

    # t=0: fresh news → the music leads in, but the news is HELD (not aired yet)
    refresh_once(state, roster, ToneWavTTS(), cache=cache, now=0.0, llm=llm)
    assert state.music_on is True and state.plan is None

    # after the 1-3 min lead-in → the news airs
    refresh_once(state, roster, ToneWavTTS(), cache=cache, now=300.0, llm=llm)
    assert state.plan is not None

    # ~1 minute after the news → the music goes silent until the next cycle
    refresh_once(state, roster, ToneWavTTS(), cache=cache, now=400.0, llm=llm)
    assert state.music_on is False
