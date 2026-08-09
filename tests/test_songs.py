"""Tests for song slots + the Spotify song wiring — offline."""

from __future__ import annotations

from statemediafm.core.director import Director
from statemediafm.core.models import NewsItem
from statemediafm.core.schedule import Cadence
from statemediafm.newsroom.tts import ToneWavTTS
from statemediafm.serve import publish_song, refresh_once
from statemediafm.songs import DEFAULT_PLAYLIST, pick
from statemediafm.spotify import SpotifyTrack
from statemediafm.web.app import _State


class _Src:
    def poll(self, since=None):
        return [NewsItem(id="1", source="hackernews", kind="story", title="X",
                         origin="Hacker News", actors=["a"])]


def test_pick_cycles_the_playlist():
    assert pick(0) == DEFAULT_PLAYLIST[0]
    assert pick(len(DEFAULT_PLAYLIST)) == DEFAULT_PLAYLIST[0]  # wraps


def test_song_due_tracks_the_song_cadence():
    d = Director(song=Cadence(600, 0))  # a song slot every 10 min
    assert d.song_due(-1.0, 0.0)  # the slot at t=0
    assert not d.song_due(0.0, 300.0)  # nothing between
    assert d.song_due(300.0, 601.0)  # the 10-min slot


def test_publish_song_resolves_via_spotify_and_advances():
    state = _State()
    calls = []

    def resolver(query, artist):
        calls.append((query, artist))
        return SpotifyTrack(name="Resolved Track", artist="Resolved Artist",
                            uri="spotify:track:ID42",
                            url="https://open.spotify.com/track/ID42",
                            preview_url="https://p/preview")

    publish_song(state, resolver)
    assert state.song["source"] == "spotify" and state.song["uri"] == "spotify:track:ID42"
    # The generic query (not a named track) is what's searched; the resolved
    # track's real name/artist are shown.
    assert calls[0] == (DEFAULT_PLAYLIST[0][1], "")
    assert (state.song["title"], state.song["artist"]) == ("Resolved Track", "Resolved Artist")
    assert state.song_i == 1
    publish_song(state, resolver)  # next slot advances the playlist
    assert calls[1] == (DEFAULT_PLAYLIST[1][1], "")


def test_publish_song_degrades_when_unresolved():
    state = _State()
    publish_song(state, lambda q, a: None)  # Spotify not connected / no match
    assert state.song["source"] is None and state.song["uri"] is None
    assert state.song["title"] == DEFAULT_PLAYLIST[0][0]  # falls back to the mood label
    assert state.song["artist"] == ""  # no named artist when unresolved


def test_refresh_once_fills_a_song_slot_when_mixing_spotify():
    state = _State()
    state.mix_spotify = True
    director = Director(song=Cadence(600, 0))
    roster = [("HN", _Src(), Cadence(900, 0), 5)]
    hits = {}

    def resolver(title, artist):
        hits["called"] = (title, artist)
        return SpotifyTrack(name=title, artist=artist, uri="spotify:track:Z",
                            url="u", preview_url=None)

    refresh_once(state, roster, ToneWavTTS(), cache={}, director=director,
                 now=1000.0, song_resolver=resolver)
    assert state.song and state.song["uri"] == "spotify:track:Z"
    assert hits["called"] == (DEFAULT_PLAYLIST[0][1], "")


def test_refresh_once_no_song_when_mix_off():
    state = _State()  # mix_spotify defaults False
    director = Director(song=Cadence(600, 0))
    roster = [("HN", _Src(), Cadence(900, 0), 5)]
    refresh_once(state, roster, ToneWavTTS(), cache={}, director=director,
                 now=1000.0, song_resolver=lambda t, a: None)
    assert state.song is None
