"""PILLAR: generative music.

``activity()`` reduces a ``NewsItem`` window to an ``ActivitySignal``;
``compose()`` renders that signal to a ``StrudelProgram`` (Strudel source text)
in a chosen style, at an intensity mapped to a brainwave band. Deterministic
end to end — no model, no audio rendered server-side.
"""

from __future__ import annotations

from .activity import activity
from .brainwave import THETA_START, band_for_intensity, intensity_from_signal
from .compose import compose
from .styles import STYLES

__all__ = [
    "STYLES",
    "THETA_START",
    "activity",
    "band_for_intensity",
    "compose",
    "intensity_from_signal",
]
