"""CLI-level tests: clean handling of source/API failures."""

from __future__ import annotations

import urllib.error

import pytest

from maelcom import cli
from maelcom.cli import _CliError, _poll


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
