"""Tests for the gitignored per-source auth config."""

from __future__ import annotations

from maelcom.auth import (
    AUTH_SOURCES,
    load_auth,
    masked_auth,
    save_auth_entry,
    source_endpoint,
    source_token,
)


def test_save_and_load_roundtrip(tmp_path):
    p = tmp_path / "a.toml"
    save_auth_entry("github", token="ghp_secrettoken123", path=p)
    save_auth_entry("jira", endpoint="https://x.atlassian.net", token="jtok", path=p)
    assert source_token("github", p) == "ghp_secrettoken123"
    assert source_endpoint("jira", p) == "https://x.atlassian.net"
    assert source_token("jira", p) == "jtok"
    assert (p.stat().st_mode & 0o777) == 0o600  # tokens: owner-only


def test_token_preserved_when_saving_endpoint_only(tmp_path):
    p = tmp_path / "a.toml"
    save_auth_entry("gitlab", token="tok1", path=p)
    save_auth_entry("gitlab", endpoint="https://gl.example.com", path=p)  # no token given
    assert source_token("gitlab", p) == "tok1"  # kept
    assert source_endpoint("gitlab", p) == "https://gl.example.com"


def test_masked_auth_hides_the_token(tmp_path):
    p = tmp_path / "a.toml"
    save_auth_entry("slack", token="xoxb-abcdef123456", path=p)
    m = masked_auth(p)
    assert set(m) == set(AUTH_SOURCES)
    assert m["slack"]["token_set"] is True
    assert m["slack"]["token_hint"].endswith("3456") and "xoxb" not in m["slack"]["token_hint"]
    assert m["github"]["token_set"] is False


def test_load_missing_is_empty(tmp_path):
    assert load_auth(tmp_path / "nope.toml") == {}
