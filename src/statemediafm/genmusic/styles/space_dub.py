"""The ``space-dub`` generator — deep dub-techno ambient parameterized by activity.

A slow, spacious dub: a sub-bass pedal, off-beat chord stabs drenched in tape
delay and reverb, a filter that breathes across the bar, and a soft hiss wash.
Calm bands sit dark and sparse; busier bands add stabs and open the filter. Uses
the shared brainwave-band traits (``band_traits``) so every band reads distinctly.

Deterministic: the same ``(signal, intensity, band, fade_ms)`` renders
byte-identical text, so it is golden-file testable. Only @strudel/web 1.0.3
primitives (no ``setcps``/``fadeIn``/``unison``).
"""

from __future__ import annotations

from ...core.models import ActivitySignal
from ..brainwave import band_traits, clamp01

# Minor-9 dub chord loops; one is chosen deterministically from the signal.
_PROGRESSIONS = (
    "<Cm9 Abmaj7 Fm9 Gm7>",
    "<Am9 Fmaj7 Dm9 Em7>",
    "<Ebmaj9 Cm9 Abmaj7 Bb7>",
)
_SUBS = (
    "<c1 ab0 f0 g0>",
    "<a0 f0 d1 e1>",
    "<eb1 c1 ab0 bb0>",
)
# Off-beat stab density, sparse → busy, keyed by band density (1,2,3,4,6).
_STABS = {
    1: "~ ~ x ~",
    2: "~ x ~ x",
    3: "~ x ~ [x x]",
    4: "[~ x] x ~ [x x]",
    6: "~ x [x x] x [~ x] x [x ~]",
}


def _seed(signal: ActivitySignal) -> int:
    theme_bits = sum(ord(c) for c in "".join(signal.themes)[:12])
    return signal.volume * 7 + signal.participant_count * 13 + int(signal.volatility * 100) + theme_bits


def render(signal: ActivitySignal, intensity: float, band: str, fade_ms: int = 2000) -> str:
    """Render a space-dub Strudel program from the signal and derived energy."""
    intensity = clamp01(intensity)
    tr = band_traits(band)
    lpf = round(tr["lpf"])
    fast = round(0.5 + intensity * 0.7, 2)  # ~0.5x (delta, glacial) → ~1.2x (gamma)

    variant = _seed(signal) % len(_PROGRESSIONS)
    prog, sub = _PROGRESSIONS[variant], _SUBS[variant]
    stab = _STABS[int(tr["density"])]

    kick_gain = round(0.2 + intensity * 0.4, 2)  # deeper kick as energy rises
    layers = [
        # Sub-bass pedal — deep and dry so the low end stays defined.
        f'  note("{sub}").s("sine").lpf(110).gain(0.5).slow(2)',
        # Off-beat chord stab: dub delay + a filter that breathes over the bar.
        (
            f'  chord("{prog}").voicing().s("sawtooth").struct("{stab}")'
            f".lpf(sine.range(280, {lpf}).slow(16)).delay(0.5).delaytime(0.375)"
            ".delayfeedback(0.55).room(0.7).roomsize(6).gain(0.24).slow(2)"
        ),
        # A slow reverb pad breath underneath, very dark.
        (
            f'  chord("{prog}").voicing().s("sawtooth").lpf(360).room(0.8)'
            ".roomsize(7).gain(0.16).slow(4)"
        ),
        # Deep kick on the one; a soft hiss wash always present.
        f'  note("c1 ~ ~ ~").s("sine").decay(0.22).sustain(0).gain({kick_gain})',
        (
            '  s("white").struct("x ~ ~ ~").decay(0.3).sustain(0).hpf(4000)'
            ".room(0.5).roomsize(4).gain(0.06)"
        ),
    ]

    header = (
        f"// statemediafm space-dub · band={band} · intensity={round(intensity, 3)} · "
        f"{signal.volume} change{'s' if signal.volume != 1 else ''}, "
        f"{signal.participant_count} voice{'s' if signal.participant_count != 1 else ''}"
    )
    body = ",\n".join(layers)
    return f"{header}\nstack(\n{body}\n).fast({fast})"
