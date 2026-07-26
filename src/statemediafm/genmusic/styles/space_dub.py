"""The ``space-dub`` generator — deep dub-techno ambient parameterized by activity.

A slow, spacious dub built as a **phrase with a shape**, not a flat loop: an acid
bass (TB-303-style — a sine-leaning sawtooth through a resonant, percussive filter
envelope) that **loops a few times, plays a turnaround, pauses, then drops** to a
clean sub boom. The chord **chime** rides the *same* phrase shape — it answers the
bass in its rests, pauses with it, and rings out over the drop — so the two feel
connected rather than independent. Movement comes from rests and silence in the
voices plus breathing filters; the drop stands alone (everything else cuts) so it
hits clean without clipping. Calm bands sit darker and loop longer; busier bands
open the filter, add stabs, and turn the phrase around sooner.

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
# The acid bass LOOP riff per variant: sparse, syncopated, lots of rests so the
# chime can answer in the gaps (the two are call-and-response, not stacked).
_BASS_LOOP = (
    "c1 ~ [~ c1] ~ eb1 ~ c1 ~",
    "a0 ~ [~ a0] ~ c1 ~ a0 ~",
    "eb1 ~ [~ eb1] ~ g0 ~ eb1 ~",
)
# The TURNAROUND: a walking fill that lifts into the pause + drop.
_BASS_TURN = (
    "c1 eb1 f1 g1 ab1 g1 f1 eb1",
    "a0 c1 d1 e1 f1 e1 d1 c1",
    "eb1 g0 ab0 bb0 c1 bb0 ab0 g0",
)
# The DROP: a clean descending sub boom, tonic dropping an octave.
_DROPS = (
    "c1 ~ c0 ~ ~ ~ ~ ~",
    "a1 ~ a0 ~ ~ ~ ~ ~",
    "eb1 ~ eb0 ~ ~ ~ ~ ~",
)
# Two evolving chime patterns (A rides the loop, B the turnaround) — hits fall in
# the bass's rests. Keyed by band density (1,2,3,4,6): sparse → busy.
_STAB_A = {
    1: "~ ~ x ~",
    2: "~ x ~ x",
    3: "~ x ~ [x x]",
    4: "[~ x] x ~ [x x]",
    6: "~ x [x x] x [~ x] x [x ~]",
}
_STAB_B = {
    1: "~ x ~ ~",
    2: "~ ~ x x",
    3: "[~ x] ~ x ~",
    4: "~ [x x] ~ x",
    6: "x ~ [x x] ~ x [x ~] ~ x",
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
    loop, turn, drop = _BASS_LOOP[variant], _BASS_TURN[variant], _DROPS[variant]
    stab_a, stab_b = _STAB_A[density], _STAB_B[density]
    loops = max(3, 7 - density)  # calm bands loop longer before turning around
    cut = round(120 + density * 60)  # acid filter base cutoff — darker when calm

    def _acid(pat: str) -> str:
        # Sine-leaning sawtooth kept dark by a resonant, percussive filter envelope
        # (TB-303 acid): the low base cutoff rounds it toward a sine; lpenv plucks
        # it open per note. Low gain to keep the sub clean (no clipping).
        return (
            f'note("{pat}").s("sawtooth").lpf(sine.range(90, {cut}).slow(6)).lpq(7)'
            ".lpenv(2.5).lpattack(0.01).lpdecay(0.16).lpsustain(0.25)"
            ".decay(0.28).sustain(0.2)"
        )

    def _chime(struct: str) -> str:
        # The chord chime, drenched in tape delay + reverb, a slow filter breath.
        return (
            f'chord("{prog}").voicing().s("sawtooth").struct("{struct}")'
            f".lpf(sine.range(400, {lpf}).slow(4)).lpq(4).delay(0.5).delaytime(0.375)"
            ".delayfeedback(0.5).room(0.7).roomsize(6).gain(0.16)"
        )

    kick_gain = round(0.15 + intensity * 0.13, 2)
    layers = [
        # BASS phrase: loop ×N → turnaround → pause → drop. The drop is a clean
        # sub sine with everything else silent around it, so it lands without mud.
        (
            f'  arrange([{loops}, {_acid(loop)}], [1, {_acid(turn)}], [1, silence], '
            f'[1, note("{drop}").s("sine").attack(0.005).decay(1.4).sustain(0)'
            ".lpf(110).gain(0.55)]).gain(0.5).slow(2)"
        ),
        # CHIME rides the SAME shape: answers the loop (A), accents the turnaround
        # (B), pauses with the bass, then one ringing hit over the drop.
        (
            f'  arrange([{loops}, {_chime(stab_a)}], [1, {_chime(stab_b)}], [1, silence], '
            f'[1, chord("{prog}").voicing().s("sawtooth").struct("x ~ ~ ~").lpf({lpf})'
            ".room(0.9).roomsize(8).delay(0.6).delaytime(0.5).delayfeedback(0.6).gain(0.14)])"
            ".slow(2)"
        ),
        # A soft kick pulses under the loop + turnaround, then cuts for the pause
        # and drop (so the drop is felt, not stepped on).
        (
            f'  arrange([{loops + 1}, note("c1 ~ ~ ~").s("sine").decay(0.2).sustain(0)'
            f".gain({kick_gain})], [2, silence]).slow(2)"
        ),
        # A quiet hiss wash, always present, gluing the space together.
        (
            '  s("white").struct("x ~ ~ ~").decay(0.3).sustain(0).hpf(4000)'
            ".room(0.5).roomsize(4).gain(0.05)"
        ),
    ]

    header = (
        f"// statemediafm space-dub · band={band} · intensity={round(intensity, 3)} · "
        f"{signal.volume} change{'s' if signal.volume != 1 else ''}, "
        f"{signal.participant_count} voice{'s' if signal.participant_count != 1 else ''}"
    )
    body = ",\n".join(layers)
    return f"{header}\nstack(\n{body}\n).fast({fast})"
