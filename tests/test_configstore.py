"""Tests for the persisted (non-secret) station settings store."""

from __future__ import annotations

import argparse

from statemediafm import cli
from statemediafm import configstore as cs
from statemediafm.web.app import _State


def test_state_config_roundtrips(tmp_path):
    p = tmp_path / "c.toml"
    st = _State()
    st.model = "Space Dub"
    st.voice = "alba"
    st.base_intensity = 0.6
    st.quiet_mode = True
    st.mix_generators = True
    st.mix_models = ["Space Dub", "Entrainment 0.1"]
    st.mix_spotify = True
    st.segments = [
        {"topic": "HN", "source": "hackernews", "every": "15m"},
        {"topic": "Eng", "source": "repo", "repo": "https://gitlab.x/y/z", "every": "10m"},
    ]

    cs.save_config(cs.state_to_config(st), p)
    loaded = cs.load_config(p)

    assert loaded["station"]["generator"] == "Space Dub"
    assert loaded["station"]["voice"] == "alba"
    assert loaded["station"]["base_intensity"] == 0.6
    assert loaded["station"]["quiet_mode"] is True
    assert loaded["station"]["refresh_s"] == 60.0
    assert loaded["mix"]["models"] == ["Space Dub", "Entrainment 0.1"]
    assert [s["topic"] for s in loaded["sources"]] == ["HN", "Eng"]

    # apply_station restores the non-flag fields onto a fresh state.
    st2 = _State()
    cs.apply_station(st2, loaded)
    assert st2.base_intensity == 0.6 and st2.quiet_mode is True
    assert st2.mix_generators is True
    assert st2.mix_models == ["Space Dub", "Entrainment 0.1"]
    assert st2.mix_spotify is True


def test_cadence_precedence_flag_over_persisted(monkeypatch, tmp_path):
    cfg = tmp_path / "c.toml"
    cfg.write_text("[station]\nnews_every_s = 300.0\nrefresh_s = 30.0\n", encoding="utf-8")
    monkeypatch.setenv("STATEMEDIAFM_CONFIG", str(cfg))
    cap = {}
    monkeypatch.setattr(cli.serve_mod, "run", lambda *a, **k: cap.update(k) or 0)

    # No cadence flags → the persisted cadence is restored.
    cli.main(["serve", "--no-open", "--tone"])
    assert cap["news_every_s"] == 300.0 and cap["refresh"] == 30.0
    # An explicit flag overrides the persisted value.
    cap.clear()
    cli.main(["serve", "--news-every", "10m", "--refresh", "5", "--no-open", "--tone"])
    assert cap["news_every_s"] == 600.0 and cap["refresh"] == 5.0


def test_cadence_persists():
    st = _State()
    st.news_every_s = 300.0
    st.refresh_s = 45.0
    station = cs.state_to_config(st)["station"]
    assert station["news_every_s"] == 300.0 and station["refresh_s"] == 45.0


def test_news_settings_roundtrip():
    st = _State()
    st.live = True
    st.news_backend = "claude-cli"
    st.news_model = "openai/gpt-4o-mini"
    st.news_temperature = 0.4
    st.news_max_tokens = 512
    cfg = cs.state_to_config(st)
    assert cfg["news"] == {"live": True, "backend": "claude-cli",
                           "model": "openai/gpt-4o-mini", "temperature": 0.4, "max_tokens": 512}


def test_default_subcommand_is_serve(monkeypatch):
    seen = {}
    monkeypatch.setattr(cli, "_serve", lambda args: seen.update(serve=args) or 0)
    monkeypatch.setattr(cli, "_genmusic", lambda args: seen.update(gm=args) or 0)

    # No subcommand → serve.
    assert cli.main([]) == 0 and "serve" in seen
    # A bare flag (no subcommand) also routes to serve, parsed as serve args.
    seen.clear()
    assert cli.main(["--port", "9999", "--no-open"]) == 0
    assert seen["serve"].port == 9999 and seen["serve"].no_open is True
    # An explicit other subcommand is not hijacked.
    seen.clear()
    assert cli.main(["genmusic", "--repo", "/tmp/x"]) == 0
    assert "gm" in seen and "serve" not in seen


def test_demo_sources_are_not_persisted():
    st = _State()
    st.segments = [
        {"topic": "HN", "source": "hackernews"},
        {"topic": "DemoRepo", "source": "repo", "repo": "x"},
    ]
    st.demo_topics = ["DemoRepo"]  # transient Demo-Mode source
    topics = {s["topic"] for s in cs.state_to_config(st)["sources"]}
    assert topics == {"HN"}


def test_missing_or_corrupt_config_degrades_to_empty(tmp_path):
    assert cs.load_config(tmp_path / "nope.toml") == {}
    bad = tmp_path / "bad.toml"
    bad.write_text("this is not = valid = toml", encoding="utf-8")
    assert cs.load_config(bad) == {}


def test_serializer_handles_scalar_types(tmp_path):
    p = tmp_path / "c.toml"
    cs.save_config(
        {
            "station": {"flag": True, "energy": 1.5, "name": 'quote"inside', "count": 3},
            "mix": {"models": ["m1", "m2"]},
            "sources": [{"topic": "T", "every": "5m"}],
        },
        p,
    )
    d = cs.load_config(p)
    assert d["station"]["flag"] is True
    assert d["station"]["energy"] == 1.5
    assert d["station"]["name"] == 'quote"inside'
    assert d["station"]["count"] == 3
    assert d["mix"]["models"] == ["m1", "m2"]
    assert d["sources"][0] == {"topic": "T", "every": "5m"}


def _serve_args(**over):
    base = {"config": None, "hn": False, "repo": None, "max_count": 25,
            "every": "15m", "token": None}
    base.update(over)
    return argparse.Namespace(**base)


def test_resolve_segments_precedence():
    persisted = {"sources": [{"topic": "Eng", "source": "repo", "repo": "https://gl/x/y",
                              "every": "10m"}]}
    # No flags → the persisted roster is restored.
    assert [s["topic"] for s in cli._resolve_segments(_serve_args(), persisted)] == ["Eng"]
    # An explicit --hn flag wins over the persisted roster.
    with_hn = cli._resolve_segments(_serve_args(hn=True), persisted)
    assert any(s["source"] == "hackernews" for s in with_hn)
    # No flags and nothing persisted → the zero-config Hacker News default.
    assert cli._resolve_segments(_serve_args(), {})[0]["source"] == "hackernews"
