"""Song slots — the familiar-songs pillar (M5).

The station drops an occasional song between bulletins. A real "matched to team
taste" source is a later step; for now the slots draw from a small set of
**generic mood/genre search seeds** (no named artists), each resolved to *some*
fitting instrumental track by the Spotify connector (:mod:`statemediafm.spotify`)
when Spotify is connected.

Each slot is a ``(label, query)`` pair: ``label`` is the mood shown on the offline
card; ``query`` is a free-text Spotify search that returns a work-appropriate,
mellow instrumental. Deterministic: ``pick(i)`` cycles the list by index, so the
running order is reproducible.
"""

from __future__ import annotations

# (label, query) — mood/genre seeds only; a mellow bed-between-news vibe. Kept
# free of specific artist/track names so the shipped default names no one.
DEFAULT_PLAYLIST: tuple[tuple[str, str], ...] = (
    ("Ambient", "ambient instrumental"),
    ("Downtempo", "downtempo instrumental"),
    ("Lo-fi", "lofi beats instrumental"),
    ("Chillhop", "chillhop instrumental"),
    ("Neoclassical", "neoclassical piano"),
    ("Deep house", "deep house instrumental"),
    ("Dub techno", "dub techno"),
    ("Drone", "drone ambient"),
    ("Trip-hop", "trip hop instrumental"),
    ("Modern classical", "modern classical strings"),
)


def pick(index: int, playlist: tuple[tuple[str, str], ...] = DEFAULT_PLAYLIST) -> tuple[str, str]:
    """The ``(label, query)`` for song slot ``index``, cycling the playlist."""
    return playlist[index % len(playlist)]
