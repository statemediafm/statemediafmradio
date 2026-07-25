"""Generative-music styles: each renders an ``ActivitySignal`` to Strudel text.

``tintinnabuli`` (the default) is a largo, Pärt-style M/T-voice piece on modified
piano; ``lofi`` is the chilled beat. Space-dub, modular-bleep and aphex-fugue
land later behind the same ``render`` signature.
"""

from __future__ import annotations

from . import lofi, tintinnabuli

# Style name → render(signal, intensity, band, fade_ms) -> str
STYLES = {"tintinnabuli": tintinnabuli.render, "lofi": lofi.render}

__all__ = ["STYLES", "lofi", "tintinnabuli"]
