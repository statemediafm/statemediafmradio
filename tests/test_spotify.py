"""Tests for the Spotify connector — offline, HTTP fully injected."""

from __future__ import annotations

import pytest

from statemediafm.spotify import SpotifyConnector, client_credentials_token, search_track

_TRACK = {
    "tracks": {"items": [{
        "name": "Ambient Piece",
        "artists": [{"name": "Test Artist"}],
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
    t = search_track("tok", "Ambient Piece", "Test Artist", http=http)
    assert t.name == "Ambient Piece" and t.artist == "Test Artist"
    assert t.uri.startswith("spotify:track:") and "open.spotify.com" in t.url
    assert t.preview_url and t.preview_url.startswith("https://")
    # the query used both the title and the artist filter (urlencode → spaces as +)
    _, url, _, _ = http.calls[0]
    assert "Ambient+Piece" in url and "Test+Artist" in url


def test_search_track_free_text_query_without_artist():
    # A mood/genre seed (no artist) is a free-text search — no track:/artist: filter.
    http = _fake_http(lambda *a: _TRACK)
    t = search_track("tok", "ambient instrumental", http=http)
    assert t.name == "Ambient Piece"
    _, url, _, _ = http.calls[0]
    assert "ambient+instrumental" in url and "track%3A" not in url


def test_search_track_returns_none_when_no_items():
    http = _fake_http(lambda *a: {"tracks": {"items": []}})
    assert search_track("tok", "Nope", http=http) is None


def test_connector_caches_the_token_and_resolves():
    def script(method, url, headers, form=None):
        return {"access_token": "T"} if "token" in url else _TRACK

    http = _fake_http(script)
    conn = SpotifyConnector("cid", "secret", http=http)
    assert conn.configured
    track = conn.resolve("Ambient Piece", "Test Artist")
    assert track.name == "Ambient Piece"
    conn.resolve("ambient instrumental")  # second call reuses the cached token
    token_calls = [c for c in http.calls if "token" in c[1]]
    assert len(token_calls) == 1  # only fetched the app token once


def test_unconfigured_connector_refuses():
    conn = SpotifyConnector("", "", http=_fake_http(lambda *a: {}))
    assert not conn.configured
    with pytest.raises(ValueError):
        conn.token()


def test_authorize_url_has_scopes_and_state():
    from statemediafm.spotify import SCOPES, authorize_url

    url = authorize_url("CID", "http://127.0.0.1:8150/spotify/callback", "xyz")
    assert url.startswith("https://accounts.spotify.com/authorize?")
    assert "client_id=CID" in url and "response_type=code" in url and "state=xyz" in url
    assert "streaming" in url and "playlist-read-private" in url  # scopes present
    assert SCOPES  # non-empty


def test_exchange_and_refresh_and_playlists():
    from statemediafm.spotify import (
        current_user,
        exchange_code,
        refresh_access_token,
        user_playlists,
    )

    http = _fake_http(lambda method, url, headers, form=None: (
        {"access_token": "A", "refresh_token": "R", "expires_in": 3600}
        if "api/token" in url and form and form.get("grant_type") == "authorization_code"
        else {"access_token": "A2", "expires_in": 3600}
        if "api/token" in url
        else {"id": "u1", "display_name": "Jamie", "product": "premium"}
        if url.endswith("/me")
        else {"items": [{"id": "p1", "name": "Focus", "uri": "spotify:playlist:p1",
                         "tracks": {"total": 42}}]}
    ))
    tok = exchange_code("c", "s", "code123", "http://cb", http=http)
    assert tok["access_token"] == "A" and tok["refresh_token"] == "R"
    assert refresh_access_token("c", "s", "R", http=http)["access_token"] == "A2"
    me = current_user("A", http=http)
    assert me == {"id": "u1", "name": "Jamie", "premium": True}
    pls = user_playlists("A", http=http)
    assert pls == [{"id": "p1", "name": "Focus", "uri": "spotify:playlist:p1", "tracks": 42}]
