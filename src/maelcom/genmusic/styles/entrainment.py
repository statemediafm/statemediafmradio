"""**Entrainment 0.1** — a new ambient generator, built from the ground up.

This is a deliberately bare starting point (version 0.1). Where *ScratchPad* grew
organically into a thick rule base, Entrainment starts as a clean canvas: a calm,
low, low-passed drone we will build up rule by rule. The name points at brainwave
entrainment — the plan's intensity→band idea — which this model will lean into.

Kept minimal and safe (only verified @strudel/web primitives): a slow evolving
low drone with a filter that breathes, over a deep sub, all ``.slow(2)`` largo.
Deterministic: the same signal always renders the same music.
"""

from __future__ import annotations

from ...core.models import ActivitySignal
from ..brainwave import clamp01

# The rule base for Entrainment — empty for now; we add rules as we build it up.
RULES: tuple[str, ...] = (
    "1. Start bare and calm: a low, low-passed drone. Build up from here.",
)

_KEY = "c"  # low C tonal centre for now


def render(signal: ActivitySignal, intensity: float, band: str, fade_ms: int = 2000) -> str:
    intensity = clamp01(intensity)
    verb = round(0.6 + (1.0 - intensity) * 0.3, 2)  # lusher reverb when calm
    # Filter breathes a little wider with activity, but stays dark and low.
    hi = round(400 + intensity * 300)

    header = (
        f"// Entrainment 0.1 · low drone (canvas) · band={band} · "
        f"intensity={round(intensity, 3)} · {signal.volume} change{'s' if signal.volume != 1 else ''}"
    )
    # A slow evolving low drone — root + fifth, filter breathing.
    drone = (
        f'  note("<[{_KEY}2,g2] [{_KEY}2,f2]>").s("sine").attack(1).release(3)'
        f".lpf(sine.range(180,{hi}).slow(12)).room({verb}).roomsize(8).gain(0.32)"
    )
    sub = f'  note("{_KEY}1").s("sine").lpf(110).attack(0.5).release(4).gain(0.28)'
    return f"{header}\nstack(\n{drone},\n{sub}\n).slow(2)"
