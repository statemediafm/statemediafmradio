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

# A gentle window on the circle of fifths, centred on A minor. Adjacent entries
# are a perfect fifth apart, so stepping +/-1 modulates by exactly one fifth and
# never drifts into remote, accidental-heavy keys.
_FIFTHS = ("G", "D", "A", "E", "B")
_HOME = _FIFTHS.index("A")

SECTION_BARS = 16
TRANSITION_BARS = 4
HISTORY_AFTER_BARS = 120
# Sections that fit before history repeats kick in (each section+transition is
# SECTION_BARS + TRANSITION_BARS bars).
FRESH_SECTIONS = HISTORY_AFTER_BARS // (SECTION_BARS + TRANSITION_BARS)
REPEAT_SECTIONS = 3


def circle_walk(seed: int, n: int) -> list[str]:
    """A gentle walk on the circle of fifths from A minor: one fifth up or down
    per section (seeded), clamped to the comfortable window."""
    idx = _HOME
    out: list[str] = []
    for i in range(n):
        out.append(_FIFTHS[idx])
        step = 1 if (seed >> i) & 1 else -1
        idx = min(len(_FIFTHS) - 1, max(0, idx + step))
    return out


def common_tone(k0: str, k1: str) -> str:
    """The pitch bridging two adjacent circle-of-fifths keys — the root of the
    quartal pivot chord. For an upward fifth (A->E) it is the new tonic; for a
    downward fifth (A->D) the old tonic; either way a tone shared by both minor
    triads. When the keys are equal (a clamped, non-moving step), it is that key.
    """
    i0, i1 = _FIFTHS.index(k0), _FIFTHS.index(k1)
    return k1 if i1 > i0 else k0


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
