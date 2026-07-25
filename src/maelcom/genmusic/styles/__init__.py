"""Generative-music styles: each renders an ``ActivitySignal`` to Strudel text.

The user-selectable **ambient generators** are named compositional *models*:

- ``Entrainment 0.1`` (default) — brainwave-entrainment ambient journeys: an
  evolving A-pedal major-chord drone (resolving to D), binaural/isochronic
  entrainment, chimes and colored-noise tide/rain waves.
- ``ScratchPad`` — the grown-up rule base: dark, low, slow-canon Dorian ambient
  with rare glints, modulation, fades and a synced sub-bass.

``lofi`` is a separate chilled-beat style (not an ambient generator). All share
the same ``render(signal, intensity, band, fade_ms) -> str`` signature.
"""

from __future__ import annotations

from collections.abc import Callable

from . import entrainment, lofi, scratchpad

# The user-selectable ambient-generator models, in display order (default first).
# A list so user/contributor generators can be registered at runtime (see
# ``maelcom.genmusic.generators``).
AMBIENT_MODELS = ["Entrainment 0.1", "ScratchPad"]

# Style/model name → render(signal, intensity, band, fade_ms) -> str
STYLES: dict[str, Callable] = {
    "Entrainment 0.1": entrainment.render,
    "ScratchPad": scratchpad.render,
    "lofi": lofi.render,
}


def register_model(name: str, render: Callable, *, ambient: bool = True) -> None:
    """Register a generator's ``render`` under ``name`` (idempotent). When
    ``ambient`` it also appears as a selectable ambient generator."""
    STYLES[name] = render
    if ambient and name not in AMBIENT_MODELS:
        AMBIENT_MODELS.append(name)


__all__ = ["AMBIENT_MODELS", "STYLES", "entrainment", "lofi", "register_model", "scratchpad"]
