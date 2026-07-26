"""Compose an ``ActivitySignal`` into a ``StrudelProgram`` (plan §5.3).

``compose`` is the pillar's published contract: pick the style renderer, derive
the felt energy (intensity → brainwave band), and wrap the Strudel text. The
intensity defaults to an activity-driven value lifted up from the user's base
level (theta at session start); pass ``intensity`` to override.
"""

import math

from ..core.models import ActivitySignal, StrudelProgram
from .brainwave import THETA_START, band_for_intensity, intensity_from_signal
from .styles import STYLES

# Selectable concert-A tuning references (Hz). 440 is standard; 435 and 433 are
# the alternative "relative" tunings.
TUNINGS = (440.0, 435.0, 433.0)


def tuning_detune_cents(tuning_a: float) -> float:
    """Cents to shift every note so concert A is ``tuning_a`` Hz instead of 440."""
    return round(1200.0 * math.log2(tuning_a / 440.0), 3)


def _apply_tuning(text: str, tuning_a: float) -> str:
    """Retune the whole program by appending a global ``.detune(cents)`` — it
    shifts every oscillator (note- and freq-based alike) by the same ratio, so
    all notes are relative to A = ``tuning_a`` Hz."""
    cents = tuning_detune_cents(tuning_a)
    return text if cents == 0.0 else f"{text}.detune({cents})"


def compose(
    signal: ActivitySignal,
    style: str = "Entrainment 0.1",
    *,
    intensity: float | None = None,
    base_intensity: float = THETA_START,
    fade_ms: int = 2000,
    tuning_a: float = 440.0,
) -> StrudelProgram:
    """Render ``signal`` to a ``StrudelProgram`` in ``style``.

    Raises ``ValueError`` for an unknown style. When ``intensity`` is ``None`` it
    is derived from the signal via ``intensity_from_signal`` (starting at
    ``base_intensity``); otherwise the caller's value is used verbatim.
    ``tuning_a`` sets the concert-A reference (440/435/432 Hz) for all notes.
    """
    if style not in STYLES:
        raise ValueError(f"Unknown style {style!r}. Known: {', '.join(sorted(STYLES))}.")

    level = intensity_from_signal(signal, base_intensity) if intensity is None else intensity
    band = band_for_intensity(level)
    text = _apply_tuning(STYLES[style](signal, level, band, fade_ms), tuning_a)
    return StrudelProgram(
        text=text,
        style=style,
        intensity=level,
        brainwave_band=band,
        fade_ms=fade_ms,
    )
