"""Tests for the configurable broadcast roster (offline: builds, never polls)."""

from __future__ import annotations

import pytest

from statemediafm.core.schedule import Cadence
from statemediafm.roster import build_roster, load_config
from statemediafm.sources.git_source import GitSource
from statemediafm.sources.hackernews import HackerNewsSource


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


def test_build_segment_and_source_kinds():
    from statemediafm.roster import build_segment, source_kinds

    topic, source, cadence, headlines = build_segment(
        {"topic": "HN", "source": "hackernews", "every": "5m"}, 0
    )
    assert topic == "HN" and isinstance(source, HackerNewsSource)
    assert cadence.every_s == 300 and headlines is None
    # An untitled segment is named by index.
    assert build_segment({"source": "hackernews"}, 2)[0] == "Segment 3"
    assert {"hackernews", "repo", "slack", "jira", "pagerduty"} <= set(source_kinds())


def test_llm_settings_reads_the_llm_table():
    from statemediafm.roster import llm_settings

    assert llm_settings({}) == {}
    assert llm_settings({"llm": {"model": "openai/x", "temperature": 0.2}}) == {
        "model": "openai/x",
        "temperature": 0.2,
    }
    # A copy, not the live config table.
    cfg = {"llm": {"model": "m"}}
    llm_settings(cfg)["model"] = "mutated"
    assert cfg["llm"]["model"] == "m"


def test_register_and_build_custom_source_kind():
    from statemediafm.roster import build_roster, register_source_kind

    class _FakeSrc:
        name = "fake"

        def poll(self, since=None):
            return []

    register_source_kind("fake_src", lambda topic, seg: _FakeSrc())
    roster = build_roster({"segments": [{"topic": "T", "source": "fake_src", "every": "5m"}]})
    assert roster[0][0] == "T" and isinstance(roster[0][1], _FakeSrc)


def test_load_source_plugins_registers_from_config_and_skips_bad():
    from statemediafm.roster import _SOURCE_KINDS, load_source_plugins

    good = load_source_plugins(
        {"source_plugins": [{"kind": "hn2", "builder": "statemediafm.roster:_build_hackernews"}]}
    )
    assert good == ["hn2"] and "hn2" in _SOURCE_KINDS
    assert load_source_plugins({"source_plugins": [{"kind": "bad", "builder": "no.mod:fn"}]}) == []
    assert "bad" not in _SOURCE_KINDS


def test_repo_source_falls_back_to_saved_auth_token(monkeypatch):
    from statemediafm import roster

    captured = {}

    def _fake_open_source(repo, max_count=20, token=None, max_age=None, gitlab_base=None):
        captured.update(token=token, max_age=max_age, gitlab_base=gitlab_base)

        class _S:
            name = "s"

            def poll(self, since=None):
                return []

        return _S()

    monkeypatch.setattr(roster, "open_source", _fake_open_source)
    monkeypatch.setattr(roster, "source_token", lambda src, path=None: "AUTHTOK" if src == "github" else None)
    monkeypatch.setattr(roster, "source_endpoint", lambda src, path=None: None)
    roster._build_repo("T", {"repo": "https://github.com/o/r"})
    assert captured["token"] == "AUTHTOK"  # pulled from the gitignored auth config
    assert captured["max_age"] == 60 * 86400  # omitted → the 60-day opened-since default

    # A max_age duration string is parsed to seconds and passed through.
    roster._build_repo("T", {"repo": "https://github.com/o/r", "max_age": "7d"})
    assert captured["max_age"] == 7 * 86400


def test_repo_source_uses_self_hosted_gitlab_endpoint(monkeypatch):
    from statemediafm import roster

    captured = {}

    def _fake_open_source(repo, max_count=20, token=None, max_age=None, gitlab_base=None):
        captured.update(repo=repo, token=token, gitlab_base=gitlab_base)

        class _S:
            name = "s"

            def poll(self, since=None):
                return []

        return _S()

    monkeypatch.setattr(roster, "open_source", _fake_open_source)
    # Configured self-hosted GitLab instance + its saved token.
    monkeypatch.setattr(roster, "source_endpoint", lambda src, path=None: "https://gitlab.corp.example" if src == "gitlab" else None)
    monkeypatch.setattr(roster, "source_token", lambda src, path=None: "GLPAT" if src == "gitlab" else None)

    roster._build_repo("T", {"repo": "https://gitlab.corp.example/team/app"})
    assert captured["gitlab_base"] == "https://gitlab.corp.example"  # threaded to open_source
    assert captured["token"] == "GLPAT"  # the URL is recognized as GitLab, so its token applies
