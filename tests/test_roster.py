"""Tests for the configurable broadcast roster (offline: builds, never polls)."""

from __future__ import annotations

import pytest

from maelcom.core.schedule import Cadence
from maelcom.roster import build_roster, load_config
from maelcom.sources.git_source import GitSource
from maelcom.sources.hackernews import HackerNewsSource


def test_build_roster_maps_sources_and_cadences():
    config = {
        "segments": [
            {"topic": "HN", "source": "hackernews", "max_count": 10, "headlines": 10,
             "every": "15m", "offset": "6m"},
            {"topic": "Repo", "source": "repo", "repo": "/local/path", "every": "10m"},
        ]
    }
    roster = build_roster(config)
    assert [t for t, _, _, _ in roster] == ["HN", "Repo"]

    (_, hn_source, hn_cadence, hn_headlines), (_, repo_source, repo_cadence, repo_headlines) = roster
    assert isinstance(hn_source, HackerNewsSource)
    assert hn_source.max_count == 10
    assert hn_cadence == Cadence(900.0, 360.0)
    assert hn_headlines == 10  # per-segment override
    # A local path routes to the git-commit source; offset defaults to 0.
    assert isinstance(repo_source, GitSource)
    assert repo_cadence == Cadence(600.0, 0.0)
    assert repo_headlines is None  # omitted → caller's default


def test_build_roster_defaults_topic_and_cadence():
    roster = build_roster({"segments": [{"source": "hackernews"}]})
    topic, _, cadence, headlines = roster[0]
    assert topic == "Segment 1"
    assert cadence == Cadence(900.0, 0.0)  # default every=15m, offset=0
    assert headlines is None


def test_build_roster_errors_are_clear():
    with pytest.raises(ValueError, match="no 'segments'"):
        build_roster({})
    with pytest.raises(ValueError, match="unknown source"):
        build_roster({"segments": [{"topic": "X", "source": "twitter"}]})
    with pytest.raises(ValueError, match="needs a 'repo'"):
        build_roster({"segments": [{"topic": "X", "source": "repo"}]})


def test_load_config_toml_and_json(tmp_path):
    toml_file = tmp_path / "roster.toml"
    toml_file.write_text(
        '[[segments]]\ntopic = "HN"\nsource = "hackernews"\nevery = "15m"\n'
    )
    cfg = load_config(toml_file)
    assert cfg["segments"][0]["topic"] == "HN"

    json_file = tmp_path / "roster.json"
    json_file.write_text('{"segments": [{"topic": "HN", "source": "hackernews"}]}')
    assert load_config(json_file)["segments"][0]["source"] == "hackernews"

    # And the parsed config builds a roster end to end.
    assert build_roster(load_config(toml_file))[0][0] == "HN"
