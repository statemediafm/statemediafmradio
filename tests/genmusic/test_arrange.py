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
    common_tone,
)

_WINDOW = {"G", "D", "A", "E", "B"}


def test_fresh_sections_fill_120_bars():
    assert (SECTION_BARS + TRANSITION_BARS) * FRESH_SECTIONS == 120


def test_circle_walk_starts_home_and_stays_in_window():
    for seed in (0, 1, 7, 42, 1000):
        walk = circle_walk(seed, 8)
        assert walk[0] == "A"  # always begins at A minor
        assert all(k in _WINDOW for k in walk)  # never drifts out of the window


def test_circle_walk_moves_by_one_fifth_each_step():
    order = ["G", "D", "A", "E", "B"]
    walk = circle_walk(0b10101, 6)  # a specific up/down pattern
    for a, b in pairwise(walk):
        assert abs(order.index(a) - order.index(b)) <= 1  # one fifth (or clamped)


def test_common_tone_is_shared_and_quartal_root():
    # Upward fifth A->E: the pivot is E (shared with A minor's triad tone E).
    assert common_tone("A", "E") == "E"
    # Downward fifth A->D: the pivot is A (shared with D minor's triad tone A).
    assert common_tone("A", "D") == "A"
    # A non-moving (clamped) step pivots on the key itself.
    assert common_tone("B", "B") == "B"


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
