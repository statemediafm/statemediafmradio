"""Spotify connector — resolves song cues to Spotify tracks via the Web API.

Uses the **Client Credentials** flow (Client ID + Secret → an app bearer token)
for catalogue search and track metadata; no user login is needed for that. Full
in-app playback of whole tracks needs the Web Playback SDK + a Premium user token
— a later step (M5); for now the connector resolves a cue to a track (URI, open
URL, and the 30-second preview when Spotify offers one).

stdlib-only (zipapp-safe). The HTTP call is injectable (``http=``) so the whole
thing is testable offline. Credentials come from the gitignored ``spotify`` auth
slot (Client ID = endpoint, Client Secret = token), entered in Settings.
"""

from __future__ import annotations

import base64
import json
import urllib.parse
import urllib.request
from dataclasses import dataclass

_TOKEN_URL = "https://accounts.spotify.com/api/token"
_SEARCH_URL = "https://api.spotify.com/v1/search"


@dataclass(frozen=True)
class SpotifyTrack:
    """A resolved track: enough to link, embed, or preview it."""

    name: str
    artist: str
    uri: str  # spotify:track:... (for the Web Playback SDK later)
    url: str  # https://open.spotify.com/track/... (link / embed)
    preview_url: str | None  # 30s mp3 preview, or None when Spotify has none


def _default_http(method: str, url: str, headers: dict, form: dict | None = None) -> dict:
    """One HTTP call returning parsed JSON. Injectable so tests never hit the network."""
    body = urllib.parse.urlencode(form).encode() if form is not None else None
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode())


def client_credentials_token(client_id: str, client_secret: str, *, http=_default_http) -> str:
    """Fetch an app access token via the Client Credentials flow."""
    basic = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    data = http(
        "POST", _TOKEN_URL,
        {"Authorization": f"Basic {basic}", "Content-Type": "application/x-www-form-urlencoded"},
        {"grant_type": "client_credentials"},
    )
    token = (data or {}).get("access_token")
    if not token:
        raise ValueError("spotify: no access_token in token response")
    return token


def search_track(token: str, title: str, artist: str | None = None, *, http=_default_http) -> SpotifyTrack | None:
    """Find the best track for ``title`` (+ optional ``artist``); None if no match."""
    q = f'track:"{title}"' + (f' artist:"{artist}"' if artist else "")
    url = _SEARCH_URL + "?" + urllib.parse.urlencode({"q": q, "type": "track", "limit": 1})
    data = http("GET", url, {"Authorization": f"Bearer {token}"})
    items = (((data or {}).get("tracks") or {}).get("items")) or []
    if not items:
        return None
    it = items[0]
    return SpotifyTrack(
        name=it.get("name", ""),
        artist=", ".join(a.get("name", "") for a in it.get("artists", [])),
        uri=it.get("uri", ""),
        url=(it.get("external_urls") or {}).get("spotify", ""),
        preview_url=it.get("preview_url"),
    )


class SpotifyConnector:
    """Credentials + a cached app token; resolves cues to tracks."""

    def __init__(self, client_id: str | None, client_secret: str | None, *, http=_default_http) -> None:
        self.client_id = (client_id or "").strip()
        self.client_secret = (client_secret or "").strip()
        self._http = http
        self._token: str | None = None

    @property
    def configured(self) -> bool:
        return bool(self.client_id and self.client_secret)

    def token(self) -> str:
        if not self.configured:
            raise ValueError("spotify: not configured (missing Client ID/Secret)")
        if not self._token:
            self._token = client_credentials_token(
                self.client_id, self.client_secret, http=self._http
            )
        return self._token

    def resolve(self, title: str, artist: str | None = None) -> SpotifyTrack | None:
        """Resolve a song title (+ artist) to a Spotify track, or None."""
        return search_track(self.token(), title, artist, http=self._http)


def from_auth(path=None, *, http=_default_http) -> SpotifyConnector:
    """Build a connector from the gitignored ``spotify`` auth slot (Client ID =
    endpoint, Client Secret = token)."""
    from .auth import source_endpoint, source_token

    return SpotifyConnector(source_endpoint("spotify", path), source_token("spotify", path), http=http)
