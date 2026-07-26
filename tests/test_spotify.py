"""Tests for the Spotify connector — offline, HTTP fully injected."""

from __future__ import annotations

import pytest

from statemediafm.spotify import SpotifyConnector, client_credentials_token, search_track

_TRACK = {
    "tracks": {"items": [{
        "name": "Teardrop",
        "artists": [{"name": "Massive Attack"}],
        "uri": "spotify:track:67Hna13dNDkZvBpTXRIaOJ",
        "external_urls": {"spotify": "https://open.spotify.com/track/67Hna13dNDkZvBpTXRIaOJ"},
        "preview_url": "https://p.scdn.co/mp3-preview/abc",
    }]}
}


def _fake_http(script):
    calls = []

    def http(method, url, headers, form=None):
        calls.append((method, url, headers, form))
        return script(method, url, headers, form)

    http.calls = calls
    return http


def test_client_credentials_token_uses_basic_auth():
    http = _fake_http(lambda *a: {"access_token": "BQtoken", "token_type": "Bearer"})
    tok = client_credentials_token("cid", "secret", http=http)
    assert tok == "BQtoken"
    method, url, headers, form = http.calls[0]
    assert method == "POST" and "accounts.spotify.com" in url
    assert headers["Authorization"].startswith("Basic ")  # base64(cid:secret)
    assert form == {"grant_type": "client_credentials"}


def test_client_credentials_token_errors_without_token():
    http = _fake_http(lambda *a: {"error": "invalid_client"})
    with pytest.raises(ValueError):
        client_credentials_token("cid", "bad", http=http)


def test_search_track_resolves_a_match():
    http = _fake_http(lambda *a: _TRACK)
    t = search_track("tok", "Teardrop", "Massive Attack", http=http)
    assert t.name == "Teardrop" and t.artist == "Massive Attack"
    assert t.uri.startswith("spotify:track:") and "open.spotify.com" in t.url
    assert t.preview_url and t.preview_url.startswith("https://")
    # the query used both the title and the artist filter (urlencode → spaces as +)
    _, url, _, _ = http.calls[0]
    assert "Teardrop" in url and "Massive+Attack" in url


def test_search_track_returns_none_when_no_items():
    http = _fake_http(lambda *a: {"tracks": {"items": []}})
    assert search_track("tok", "Nope", http=http) is None


def test_connector_caches_the_token_and_resolves():
    def script(method, url, headers, form=None):
        return {"access_token": "T"} if "token" in url else _TRACK

    http = _fake_http(script)
    conn = SpotifyConnector("cid", "secret", http=http)
    assert conn.configured
    track = conn.resolve("Teardrop", "Massive Attack")
    assert track.name == "Teardrop"
    conn.resolve("Teardrop")  # second call reuses the cached token
    token_calls = [c for c in http.calls if "token" in c[1]]
    assert len(token_calls) == 1  # only fetched the app token once


def test_unconfigured_connector_refuses():
    conn = SpotifyConnector("", "", http=_fake_http(lambda *a: {}))
    assert not conn.configured
    with pytest.raises(ValueError):
        conn.token()
