"""CLI-level tests: clean handling of source/API failures."""

from __future__ import annotations

import urllib.error

import pytest

from statemediafm import cli
from statemediafm.cli import _CliError, _poll


class _Boom:
    """A source whose poll() raises, to exercise error handling."""

    def __init__(self, exc: Exception):
        self.exc = exc

    def poll(self, since=None):
        raise self.exc


def test_poll_wraps_rate_limit_as_clean_error():
    err = urllib.error.HTTPError("u", 403, "rate limit exceeded", {}, None)  # type: ignore[arg-type]
    with pytest.raises(_CliError) as excinfo:
        _poll(_Boom(err))
    msg = str(excinfo.value)
    assert "403" in msg
    assert "GITHUB_TOKEN" in msg  # actionable hint, not a traceback


def test_poll_wraps_network_error():
    with pytest.raises(_CliError, match="cannot reach"):
        _poll(_Boom(urllib.error.URLError("down")))


def test_main_reports_source_error_without_traceback(capsys, monkeypatch):
    def boom(self, since=None):
        raise urllib.error.HTTPError("u", 403, "rate limit exceeded", {}, None)  # type: ignore[arg-type]

    monkeypatch.setattr(cli.HackerNewsSource, "poll", boom)
    rc = cli.main(["demo", "--hn"])
    assert rc == 1
    err = capsys.readouterr().err
    assert err.startswith("error:")
    assert "403" in err


def test_rundown_prints_the_hour_of_radio(capsys):
    # The M4 "hour of radio" demo: news on the 17-min cadence, songs, idents, and
    # a felt-cadence guarantee — deterministic in structure regardless of the clock.
    rc = cli.main(["rundown", "--news-every", "17m", "--window", "60"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "hour of radio" in out
    assert out.count("● News bulletin") == 4  # 0, 17, 34, 51 min
    assert "♪ Song slot" in out and "Station ident" in out
    assert "[OK]" in out  # felt cadence within the cap
    assert "[TOO LONG]" not in out


def test_serve_defaults_to_hacker_news_with_no_source():
    import argparse
    # Bare `serve` (no --config/--hn/--repo) demos the HN front page by default.
    args = argparse.Namespace(config=None, hn=False, repo=None, token=None,
                              max_count=25, every="15m")
    segs = cli._resolve_segments(args)
    assert [s["source"] for s in segs] == ["hackernews"]
    # But `--repo X` alone does NOT pull in HN.
    args2 = argparse.Namespace(config=None, hn=False, repo="https://github.com/o/r",
                              token=None, max_count=25, every="15m")
    assert [s["source"] for s in cli._resolve_segments(args2)] == ["repo"]
