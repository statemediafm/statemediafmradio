"""Song slots — the familiar-songs pillar (M5).

The station drops an occasional song between bulletins. A real "matched to team
taste" source is a later step; for now the slots draw from a small curated
default playlist of broadly-familiar, work-appropriate, mellow tracks, and each
slot's ``(title, artist)`` is resolved to a streamable track by the Spotify
connector (:mod:`statemediafm.spotify`) when Spotify is connected.

Deterministic: ``pick(i)`` cycles the playlist by index, so the running order is
reproducible.
"""

from __future__ import annotations

# (title, artist) — mellow, familiar, safe-for-work; a bed-between-news vibe.
DEFAULT_PLAYLIST: tuple[tuple[str, str], ...] = (
    ("Teardrop", "Massive Attack"),
    ("Porcelain", "Moby"),
    ("Intro", "The xx"),
    ("Midnight City", "M83"),
    ("Strobe", "deadmau5"),
    ("An Ending (Ascent)", "Brian Eno"),
    ("Svefn-g-englar", "Sigur Rós"),
    ("Nightcall", "Kavinsky"),
    ("Weightless", "Marconi Union"),
    ("Avril 14th", "Aphex Twin"),
)


def pick(index: int, playlist: tuple[tuple[str, str], ...] = DEFAULT_PLAYLIST) -> tuple[str, str]:
    """The ``(title, artist)`` for song slot ``index``, cycling the playlist."""
    return playlist[index % len(playlist)]
