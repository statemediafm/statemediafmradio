"""Generative-music styles: each renders an ``ActivitySignal`` to Strudel text.

M2 ships one style (``lofi``). Space-dub, modular-bleep, tintinnabuli, and
aphex-fugue land in M4 behind the same ``render`` signature.
"""

from __future__ import annotations

from . import lofi

# Style name → render(signal, intensity, band, fade_ms) -> str
STYLES = {"lofi": lofi.render}

__all__ = ["STYLES", "lofi"]
