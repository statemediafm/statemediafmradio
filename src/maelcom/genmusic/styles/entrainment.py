"""**Entrainment 0.1** — a new ambient generator, built from the ground up.

The idea in the name: **brainwave entrainment**. Maelcom already maps activity to
a felt ``intensity`` and thence to a brainwave *band* (delta…gamma); Entrainment
turns that into sound designed to nudge the listener toward that state — the
music pulses and beats at the band's characteristic frequency.

Foundation (v0.1), two complementary mechanisms:

- **Binaural** — two pure sine carriers a few Hz apart, panned hard left/right.
  The brain perceives their *difference* as a beat at the target frequency. This
  difference is an exact number of Hz regardless of Strudel's tempo, so it is
  accurate; it needs headphones.
- **Isochronic** — the same target frequency as an amplitude throb on a centre
  tone, which works on speakers. Its rate depends on the cycle length (see
  ``_CYCLE_S``), so it is calibrated, not exact.

Busier activity → a higher band → a faster beat, so the target rises with the
day's energy. Deterministic. No largo ``.slow`` here — the beat rate must stay
accurate. We build this up rule by rule from here.
"""

from __future__ import annotations

from ...core.models import ActivitySignal
from ..brainwave import clamp01

# The rule base for Entrainment — we add rules as we build it up.
RULES: tuple[str, ...] = (
    "1. Entrainment: pulse/beat at the current brainwave band's frequency to nudge the listener toward it.",
    "2. Band → frequency: delta 2, theta 6, alpha 10, beta 20, gamma 40 Hz; busier activity → higher band → faster beat.",
    "3. Two mechanisms: a BINAURAL detuned carrier pair (L/R, exact, headphones) and an ISOCHRONIC amplitude throb (speakers).",
    "4. Calm & low: a soft ~110 Hz carrier over a grounding sub; nothing harsh.",
    "5. Deterministic; no largo .slow — the beat rate must stay accurate.",
)

# EEG band centre frequencies (Hz) — the entrainment targets.
_BAND_HZ = {"delta": 2.0, "theta": 6.0, "alpha": 10.0, "beta": 20.0, "gamma": 40.0}
_CARRIER_HZ = 110.0  # a low A carrier — comfortable, well below anything harsh
# Assumed seconds per Strudel cycle (cps 0.5 default). Only the ISOCHRONIC pulse
# rate depends on this; the binaural beat is exact. Tune here if the throb rate
# is off (the engine doesn't expose cps to read back).
_CYCLE_S = 2.0


def render(signal: ActivitySignal, intensity: float, band: str, fade_ms: int = 2000) -> str:
    intensity = clamp01(intensity)
    beat = _BAND_HZ.get(band, 6.0)  # target entrainment frequency (Hz)
    f = _CARRIER_HZ
    fr = round(f + beat, 3)  # right-ear carrier: exactly `beat` Hz above the left
    n_iso = max(1, round(beat * _CYCLE_S))  # isochronic pulses per cycle
    sub = round(f / 2.0, 3)  # a grounding sub an octave below

    header = (
        f"// Entrainment 0.1 · {band} @ {beat:g} Hz · carrier {f:g}/{fr:g} Hz (binaural) "
        f"+ isochronic · intensity={round(intensity, 3)}"
    )
    layers = [
        # Binaural pair — a `beat` Hz beat perceived between the ears (headphones).
        f'  freq({f:g}).s("sine").pan(0).attack(0.5).release(0.5).gain(0.2)',
        f'  freq({fr:g}).s("sine").pan(1).attack(0.5).release(0.5).gain(0.2)',
        # Isochronic — the same `beat` Hz felt as an amplitude throb (speakers).
        f'  freq({f:g}).s("sine").gain(sine.range(0.02,0.24).fast({n_iso}))',
        # A soft grounding sub an octave down.
        f'  freq({sub:g}).s("sine").lpf(120).attack(1).release(1).gain(0.14)',
    ]
    return f"{header}\nstack(\n" + ",\n".join(layers) + "\n)"
