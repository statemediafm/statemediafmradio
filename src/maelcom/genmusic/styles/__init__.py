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

from . import entrainment, lofi, scratchpad

# The user-selectable ambient-generator models, in display order (default first).
AMBIENT_MODELS = ("Entrainment 0.1", "ScratchPad")

# Style/model name → render(signal, intensity, band, fade_ms) -> str
STYLES = {
    "Entrainment 0.1": entrainment.render,
    "ScratchPad": scratchpad.render,
    "lofi": lofi.render,
}

__all__ = ["AMBIENT_MODELS", "STYLES", "entrainment", "lofi", "scratchpad"]
