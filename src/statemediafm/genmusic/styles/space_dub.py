"""The ``space-dub`` generator — deep dub-techno ambient parameterized by activity.

A slow, spacious dub: an **evolving** syncopated dub-bass (an ``arrange`` of riffs
that morphs over bars, its filter breathing and gain pumping for movement),
off-beat chord stabs drenched in tape delay and reverb, a swelling dark pad, and
a **very occasional sub-bass drop** — a rare descending deep boom. Calm bands sit
dark and sparse; busier bands open the filter, add stabs, and evolve the bass
faster. Uses the shared brainwave-band traits (``band_traits``).

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
# Evolving dub-bass riffs per variant (three 8-step phrases, low and syncopated).
# ``arrange`` walks them so the loop mutates rather than merely repeating.
_BASS_RIFFS = (
    ("c1 ~ ~ c1 ~ ab0 ~ ~", "c1 ~ ab0 ~ ~ f0 ~ g0", "c1 ~ ~ eb1 ~ ab0 ~ ~"),
    ("a0 ~ ~ a0 ~ f0 ~ ~", "a0 ~ f0 ~ ~ d1 ~ e1", "a0 ~ ~ c1 ~ f0 ~ ~"),
    ("eb1 ~ ~ eb1 ~ c1 ~ ~", "eb1 ~ c1 ~ ~ ab0 ~ bb0", "eb1 ~ ~ g0 ~ c1 ~ ~"),
)
# The rare sub-drop: a descending deep boom (tonic → an octave below).
_DROPS = ("c1 ~ ~ ~ c0 ~ ~ ~", "a0 ~ ~ ~ f0 ~ ~ ~", "eb1 ~ ~ ~ eb0 ~ ~ ~")
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
    density = int(tr["density"])
    lpf = round(tr["lpf"])
    fast = round(0.5 + intensity * 0.7, 2)  # ~0.5x (delta, glacial) → ~1.2x (gamma)

    variant = _seed(signal) % len(_PROGRESSIONS)
    prog = _PROGRESSIONS[variant]
    r0, r1, r2 = _BASS_RIFFS[variant]
    drop = _DROPS[variant]
    stab = _STABS[density]
    seg = max(2, 8 - density)  # higher energy evolves the bass faster

    def _b(riff: str) -> str:
        return f'note("{riff}").s("sine").decay(0.5).sustain(0.2)'

    kick_gain = round(0.18 + intensity * 0.34, 2)
    layers = [
        # Evolving dub bass: an arrange of riffs (r0 r1 r2 r1) that morphs over
        # bars; the filter breathes and the gain pumps for constant movement.
        (
            f'  arrange([{seg}, {_b(r0)}], [{seg}, {_b(r1)}], '
            f'[{seg}, {_b(r2)}], [{seg}, {_b(r1)}])'
            f".lpf(sine.range(70, 200).slow(24)).gain(sine.range(0.4, 0.55).slow(8)).slow(2)"
        ),
        # Off-beat chord stab: dub delay, a breathing filter, and slow auto-pan.
        (
            f'  chord("{prog}").voicing().s("sawtooth").struct("{stab}")'
            f".lpf(sine.range(280, {lpf}).slow(16)).delay(0.5).delaytime(0.375)"
            ".delayfeedback(0.55).room(0.7).roomsize(6).pan(sine.range(0.35, 0.65).slow(11))"
            ".gain(0.22).slow(2)"
        ),
        # A slow reverb pad breath underneath, dark, swelling in and out.
        (
            f'  chord("{prog}").voicing().s("sawtooth").lpf(360).room(0.8).roomsize(7)'
            ".gain(sine.range(0.1, 0.2).slow(20)).slow(4)"
        ),
        # A soft kick for pulse; a quiet hiss wash always present.
        f'  note("c1 ~ ~ ~").s("sine").decay(0.2).sustain(0).gain({kick_gain})',
        (
            '  s("white").struct("x ~ ~ ~").decay(0.3).sustain(0).hpf(4000)'
            ".room(0.5).roomsize(4).gain(0.06)"
        ),
        # Very occasional sub-bass drop: ~31 bars of silence, then a deep descending
        # boom that swoops an octave down with a long tail.
        (
            f'  arrange([31, silence], [1, note("{drop}").s("sine")'
            ".attack(0.005).decay(1.6).sustain(0).lpf(90).gain(0.72)])"
        ),
    ]

    header = (
        f"// statemediafm space-dub · band={band} · intensity={round(intensity, 3)} · "
        f"{signal.volume} change{'s' if signal.volume != 1 else ''}, "
        f"{signal.participant_count} voice{'s' if signal.participant_count != 1 else ''}"
    )
    body = ",\n".join(layers)
    return f"{header}\nstack(\n{body}\n).fast({fast})"
