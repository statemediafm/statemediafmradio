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
    assert loaded["mix"]["models"] == ["Space Dub", "Entrainment 0.1"]
    assert [s["topic"] for s in loaded["sources"]] == ["HN", "Eng"]

    # apply_station restores the non-flag fields onto a fresh state.
    st2 = _State()
    cs.apply_station(st2, loaded)
    assert st2.base_intensity == 0.6 and st2.quiet_mode is True
    assert st2.mix_generators is True
    assert st2.mix_models == ["Space Dub", "Entrainment 0.1"]
    assert st2.mix_spotify is True


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
