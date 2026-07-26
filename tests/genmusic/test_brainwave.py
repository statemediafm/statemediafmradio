"""Tests for the intensity ↔ brainwave-band mapping."""

from __future__ import annotations

from statemediafm.core.models import ActivitySignal
from statemediafm.genmusic.brainwave import (
    THETA_START,
    band_for_intensity,
    clamp01,
    intensity_from_signal,
)


def _signal(volume: int, participants: int, volatility: float = 0.0) -> ActivitySignal:
    return ActivitySignal(
        window_s=0.0,
        volume=volume,
        volatility=volatility,
        participant_count=participants,
    )


def test_clamp01_bounds():
    assert clamp01(-1.0) == 0.0
    assert clamp01(2.0) == 1.0
    assert clamp01(0.3) == 0.3


def test_band_ordering_across_the_axis():
    assert band_for_intensity(0.0) == "delta"
    assert band_for_intensity(0.1) == "delta"
    assert band_for_intensity(0.25) == "theta"
    assert band_for_intensity(0.5) == "alpha"
    assert band_for_intensity(0.7) == "beta"
    assert band_for_intensity(0.9) == "gamma"
    # Out-of-range clamps rather than raising.
    assert band_for_intensity(5.0) == "gamma"
    assert band_for_intensity(-5.0) == "delta"


def test_theta_start_is_a_theta_intensity():
    assert band_for_intensity(THETA_START) == "theta"


def test_quiet_signal_stays_at_base():
    # Zero activity → intensity is exactly the base (theta at session start).
    assert intensity_from_signal(_signal(0, 0)) == THETA_START


def test_busier_signal_raises_intensity_monotonically():
    calm = intensity_from_signal(_signal(1, 1))
    busy = intensity_from_signal(_signal(50, 10, volatility=0.8))
    assert busy > calm
    assert busy <= 1.0
    # More participants alone lifts it.
    assert intensity_from_signal(_signal(10, 6)) > intensity_from_signal(_signal(10, 1))


def test_base_intensity_shifts_the_floor():
    low = intensity_from_signal(_signal(5, 2), base_intensity=0.1)
    high = intensity_from_signal(_signal(5, 2), base_intensity=0.6)
    assert high > low
    assert high <= 1.0
