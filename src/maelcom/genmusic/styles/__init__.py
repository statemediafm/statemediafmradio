"""Generative-music styles: each renders an ``ActivitySignal`` to Strudel text.

The user-selectable **ambient generators** are named compositional *models*:

- ``ScratchPad`` (default) — the grown-up rule base: dark, low, slow-canon
  Dorian ambient with rare glints, modulation, fades and a synced sub-bass.
- ``Entrainment 0.1`` — a fresh model built from the ground up (a bare canvas).

``lofi`` is a separate chilled-beat style (not an ambient generator). All share
the same ``render(signal, intensity, band, fade_ms) -> str`` signature.
"""

from __future__ import annotations

from . import entrainment, lofi, scratchpad

# The user-selectable ambient-generator models, in display order.
AMBIENT_MODELS = ("ScratchPad", "Entrainment 0.1")

# Style/model name → render(signal, intensity, band, fade_ms) -> str
STYLES = {
    "ScratchPad": scratchpad.render,
    "Entrainment 0.1": entrainment.render,
    "lofi": lofi.render,
}

__all__ = ["AMBIENT_MODELS", "STYLES", "entrainment", "lofi", "scratchpad"]
