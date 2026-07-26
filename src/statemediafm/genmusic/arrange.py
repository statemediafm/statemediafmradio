"""Arrangement engine — how the music evolves over time.

A piece is a sequence of **16-bar sections** joined by **4-bar transitions**.
Each transition modulates one step around the **circle of fifths** and is built
from three arpeggios — the current chord, a *pivot* chord that shares tones with
both keys (a quartal chord on the common tone, i.e. 4ths/5ths), and the new
chord — before the tintinnabuli voices settle into the new key.

The first sections walk the circle for the first ~120 bars; after that, sections
are drawn back from history for deterministic "random" repeats of earlier
sequences and their transitions. Everything derives from the signal seed, so the
whole arrangement renders byte-identical.
"""

from __future__ import annotations

# A wide window on the circle of fifths, centred on A minor. Adjacent entries are
# a perfect fifth apart; the walk may step one OR two fifths at a time, so it
# roams to fairly remote keys (C .. F#) for more adventurous modulations.
_FIFTHS = ("C", "G", "D", "A", "E", "B", "F#")
_HOME = _FIFTHS.index("A")

SECTION_BARS = 16
TRANSITION_BARS = 4
HISTORY_AFTER_BARS = 120
# Sections that fit before history repeats kick in (each section+transition is
# SECTION_BARS + TRANSITION_BARS bars).
FRESH_SECTIONS = HISTORY_AFTER_BARS // (SECTION_BARS + TRANSITION_BARS)
REPEAT_SECTIONS = 3


def circle_walk(seed: int, n: int) -> list[str]:
    """An adventurous walk on the circle of fifths from A minor: one OR two
    fifths up or down per section (seeded), clamped to the window."""
    idx = _HOME
    out: list[str] = []
    for i in range(n):
        out.append(_FIFTHS[idx])
        mag = 2 if (seed >> (i + 8)) & 1 else 1  # sometimes leap two fifths
        step = mag if (seed >> i) & 1 else -mag
        idx = min(len(_FIFTHS) - 1, max(0, idx + step))
    return out


def pivot_key(k0: str, k1: str) -> str:
    """The bridging key for a transition — the root of the quartal pivot chord,
    whose 4th/5th voicing shares tones with both keys. It is the circle-of-fifths
    entry midway between the two (biased toward the destination), so for a single
    fifth it is a neighbour and for a two-fifth leap it is the key in between.
    When the keys are equal (a clamped, non-moving step), it is that key."""
    i0, i1 = _FIFTHS.index(k0), _FIFTHS.index(k1)
    if i1 == i0:
        return k0
    mid = (i0 + i1 + 1) // 2 if i1 > i0 else (i0 + i1) // 2
    return _FIFTHS[mid]


def build_plan(
    seed: int,
    fresh: int = FRESH_SECTIONS,
    repeat: int = REPEAT_SECTIONS,
) -> list[dict]:
    """The play order of sections. The first ``fresh`` sections walk the circle
    (~120 bars); the next ``repeat`` are drawn back from history for deterministic
    repeats. Each unit carries its key, a melody ``salt`` (a repeated section
    index reuses its salt, so the melody repeats verbatim), and ``next`` — the key
    it transitions into (the following unit's key, wrapping so the arrangement
    loops seamlessly).
    """
    fresh = max(1, fresh)
    walk = circle_walk(seed, fresh)
    order = list(range(fresh))
    for j in range(max(0, repeat)):
        order.append((seed * 3 + j * 7) % fresh)  # deterministic pick from history

    keys = [walk[i] for i in order]
    salts = [i * 101 for i in order]  # repeated index -> same salt -> same melody
    plan: list[dict] = []
    for pos, key in enumerate(keys):
        nxt = keys[(pos + 1) % len(keys)]
        plan.append({"key": key, "salt": salts[pos], "next": nxt})
    return plan
