"""Control-API security: session-token auth + Host/Origin allowlist.

The app is loopback + single-operator, but a served port is reachable by any web
page the operator visits (CSRF / DNS rebinding). A :class:`SecurityPolicy` closes
that: a per-session token on every non-public route, and a Host/Origin lock.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from statemediafm.web.app import (
    SecurityPolicy,
    _host_only,
    create_app,
    new_security_policy,
)

TOKEN = "test-session-token"
# TestClient sends Host: testserver — allow it alongside loopback so the client works.
POLICY = SecurityPolicy(token=TOKEN, allowed_hosts=frozenset({"testserver", "127.0.0.1", "localhost"}))


def _client(policy=POLICY):
    return TestClient(create_app(security=policy))


# ── Public routes need no token ──────────────────────────────────────────────


def test_page_is_public_and_embeds_the_token():
    r = _client().get("/")
    assert r.status_code == 200
    assert TOKEN in r.text  # embedded for the same-origin fetch wrapper
    assert "X-SMFM-Token" in r.text


def test_health_is_public_even_when_secured():
    assert _client().get("/health").status_code == 200


# ── Guarded routes require the token ─────────────────────────────────────────


def test_guarded_get_without_token_is_401():
    assert _client().get("/genmusic").status_code == 401


def test_guarded_get_with_token_header_is_200():
    r = _client().get("/genmusic", headers={"X-SMFM-Token": TOKEN})
    assert r.status_code == 200


def test_guarded_post_without_token_is_401():
    assert _client().post("/broadcast", params={"on": True}).status_code == 401


def test_guarded_post_with_token_is_200():
    r = _client().post("/broadcast", params={"on": True}, headers={"X-SMFM-Token": TOKEN})
    assert r.status_code == 200


def test_bearer_authorization_header_also_works():
    r = _client().get("/genmusic", headers={"Authorization": f"Bearer {TOKEN}"})
    assert r.status_code == 200


def test_wrong_token_is_401():
    assert _client().get("/genmusic", headers={"X-SMFM-Token": "nope"}).status_code == 401


# ── Host / Origin allowlist ──────────────────────────────────────────────────


def test_unknown_host_is_403_dns_rebinding_defense():
    # A DNS-rebinding request arrives with the attacker's Host header.
    r = _client().get("/health", headers={"Host": "evil.example.com"})
    assert r.status_code == 403


def test_cross_origin_request_is_403():
    r = _client().get(
        "/genmusic",
        headers={"X-SMFM-Token": TOKEN, "Origin": "http://evil.example.com"},
    )
    assert r.status_code == 403


def test_same_origin_request_passes():
    r = _client().get(
        "/genmusic",
        headers={"X-SMFM-Token": TOKEN, "Origin": "http://testserver"},
    )
    assert r.status_code == 200


# ── Unsecured (default) app is unchanged — back-compat for tests/embedders ────


def test_no_policy_leaves_the_api_open():
    client = TestClient(create_app())  # no security policy
    assert client.get("/genmusic").status_code == 200
    assert client.post("/broadcast", params={"on": True}).status_code == 200


# ── Policy factory ───────────────────────────────────────────────────────────


def test_new_security_policy_has_a_token_and_loopback_hosts():
    p = new_security_policy(host="127.0.0.1")
    assert len(p.token) >= 32
    assert {"127.0.0.1", "localhost", "::1"} <= p.allowed_hosts


def test_new_security_policy_drops_wildcard_bind_host():
    p = new_security_policy(host="0.0.0.0")
    assert "0.0.0.0" not in p.allowed_hosts  # not a valid Host value
    assert "127.0.0.1" in p.allowed_hosts


def test_host_only_strips_port_and_ipv6_brackets():
    assert _host_only("127.0.0.1:8150") == "127.0.0.1"
    assert _host_only("[::1]:80") == "::1"
    assert _host_only("localhost") == "localhost"
