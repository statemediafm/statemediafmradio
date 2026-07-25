"""Tests for the arrangement engine (circle-of-fifths plan + history repeats)."""

from __future__ import annotations

from itertools import pairwise

from maelcom.genmusic.arrange import (
    FRESH_SECTIONS,
    REPEAT_SECTIONS,
    SECTION_BARS,
    TRANSITION_BARS,
    build_plan,
    circle_walk,
    pivot_key,
)

_WINDOW = {"C", "G", "D", "A", "E", "B", "F#"}
_ORDER = ["C", "G", "D", "A", "E", "B", "F#"]


def test_fresh_sections_fill_120_bars():
    assert (SECTION_BARS + TRANSITION_BARS) * FRESH_SECTIONS == 120


def test_circle_walk_starts_home_and_stays_in_window():
    for seed in (0, 1, 7, 42, 1000):
        walk = circle_walk(seed, 8)
        assert walk[0] == "A"  # always begins at A minor
        assert all(k in _WINDOW for k in walk)  # never drifts out of the window


def test_circle_walk_moves_by_at_most_two_fifths_each_step():
    walk = circle_walk(0b10101, 6)  # a specific up/down pattern
    for a, b in pairwise(walk):
        assert abs(_ORDER.index(a) - _ORDER.index(b)) <= 2  # one or two fifths (or clamped)


def test_pivot_key_is_between_and_shares_tones():
    # Single fifth up A->E: the pivot is the neighbour E.
    assert pivot_key("A", "E") == "E"
    # Single fifth down A->D: the pivot is D.
    assert pivot_key("A", "D") == "D"
    # Two-fifth leap A->B: the pivot is the key in between (E).
    assert pivot_key("A", "B") == "E"
    # A non-moving (clamped) step pivots on the key itself.
    assert pivot_key("B", "B") == "B"


def test_build_plan_length_and_history_repeats():
    plan = build_plan(seed=12345)
    assert len(plan) == FRESH_SECTIONS + REPEAT_SECTIONS
    # Every transition target is the next section's key (loops at the end).
    for i, unit in enumerate(plan):
        assert unit["next"] == plan[(i + 1) % len(plan)]["key"]


def test_history_repeats_reproduce_earlier_sections_verbatim():
    plan = build_plan(seed=999)
    fresh = plan[:FRESH_SECTIONS]
    fresh_by_salt = {u["salt"]: u["key"] for u in fresh}
    # A repeated section reuses a fresh section's salt AND its key (a true repeat
    # of the sequence, not just a re-modulation).
    for repeated in plan[FRESH_SECTIONS:]:
        assert repeated["salt"] in fresh_by_salt
        assert repeated["key"] == fresh_by_salt[repeated["salt"]]


def test_build_plan_is_deterministic():
    assert build_plan(seed=77) == build_plan(seed=77)
