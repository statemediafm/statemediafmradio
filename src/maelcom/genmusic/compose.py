"""Compose an ``ActivitySignal`` into a ``StrudelProgram`` (plan §5.3).

``compose`` is the pillar's published contract: pick the style renderer, derive
the felt energy (intensity → brainwave band), and wrap the Strudel text. The
intensity defaults to an activity-driven value lifted up from the user's base
level (theta at session start); pass ``intensity`` to override.
"""

from __future__ import annotations

from ..core.models import ActivitySignal, StrudelProgram
from .brainwave import THETA_START, band_for_intensity, intensity_from_signal
from .styles import STYLES


def compose(
    signal: ActivitySignal,
    style: str = "Entrainment 0.1",
    *,
    intensity: float | None = None,
    base_intensity: float = THETA_START,
    fade_ms: int = 2000,
) -> StrudelProgram:
    """Render ``signal`` to a ``StrudelProgram`` in ``style``.

    Raises ``ValueError`` for an unknown style. When ``intensity`` is ``None`` it
    is derived from the signal via ``intensity_from_signal`` (starting at
    ``base_intensity``); otherwise the caller's value is used verbatim.
    """
    if style not in STYLES:
        raise ValueError(f"Unknown style {style!r}. Known: {', '.join(sorted(STYLES))}.")

    level = intensity_from_signal(signal, base_intensity) if intensity is None else intensity
    band = band_for_intensity(level)
    text = STYLES[style](signal, level, band, fade_ms)
    return StrudelProgram(
        text=text,
        style=style,
        intensity=level,
        brainwave_band=band,
        fade_ms=fade_ms,
    )
