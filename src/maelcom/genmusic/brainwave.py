"""Intensity ↔ brainwave-band mapping (plan §5.3).

Bands, from calmest to most focused: ``delta`` < ``theta`` < ``alpha`` <
``beta`` < ``gamma``. A session's *felt energy* is a single ``intensity`` in
[0, 1]; this module maps that to a band and derives an intensity from an
``ActivitySignal`` plus a user base level. Sessions start at **theta** and
adapt upward as activity rises.
"""

from __future__ import annotations

from ..core.models import ActivitySignal

# Lower bound of each band on the 0..1 intensity axis, calmest first.
_BANDS: tuple[tuple[str, float], ...] = (
    ("delta", 0.00),
    ("theta", 0.15),
    ("alpha", 0.40),
    ("beta", 0.60),
    ("gamma", 0.85),
)

# Sessions begin here — the low end of theta (plan §5.3).
THETA_START = 0.25

# The full brainwave-band mapping (plan §5.3): each band's shared *musical
# traits*, from calmest (delta) to most focused (gamma). Generators read these so
# every band renders distinctly and consistently — ``carrier_hz`` is the
# entrainment pulse rate, ``density`` the note/voice busyness, ``lpf`` the
# brightness ceiling (Hz), ``motion`` the tempo multiplier. Monotonic in each.
BAND_TRAITS: dict[str, dict[str, float]] = {
    "delta": {"carrier_hz": 2.0, "density": 1, "lpf": 300, "motion": 0.5},
    "theta": {"carrier_hz": 6.0, "density": 2, "lpf": 500, "motion": 0.7},
    "alpha": {"carrier_hz": 10.0, "density": 3, "lpf": 800, "motion": 1.0},
    "beta": {"carrier_hz": 20.0, "density": 4, "lpf": 1200, "motion": 1.4},
    "gamma": {"carrier_hz": 40.0, "density": 6, "lpf": 1800, "motion": 1.9},
}


def band_traits(band: str) -> dict[str, float]:
    """The musical traits for ``band`` (a copy; defaults to theta if unknown)."""
    return dict(BAND_TRAITS.get(band, BAND_TRAITS["theta"]))


def clamp01(x: float) -> float:
    """Clamp ``x`` to the closed unit interval."""
    return max(0.0, min(1.0, x))


def band_for_intensity(intensity: float) -> str:
    """Return the brainwave band containing ``intensity`` (clamped to [0, 1])."""
    intensity = clamp01(intensity)
    band = _BANDS[0][0]
    for name, low in _BANDS:
        if intensity >= low:
            band = name
    return band


def intensity_from_signal(
    signal: ActivitySignal, base_intensity: float = THETA_START
) -> float:
    """Derive a 0..1 intensity from activity, lifting up from ``base_intensity``.

    A quiet window stays at the base (theta by default); busier, more
    multi-participant, burstier windows rise toward beta/gamma. Deterministic
    and monotonic in each input, with diminishing returns on raw volume so a
    very active repo doesn't peg instantly.
    """
    base = clamp01(base_intensity)
    # Soft-saturating volume term: ~0.5 at 10 items, approaching 1 for many.
    volume_term = 1.0 - 1.0 / (1.0 + signal.volume / 10.0)
    participant_term = min(signal.participant_count, 8) / 8.0
    lift = 0.5 * volume_term + 0.3 * participant_term + 0.2 * clamp01(signal.volatility)
    # Lift consumes the remaining headroom above the base, so output stays ≤ 1.
    return clamp01(base + lift * (1.0 - base))
