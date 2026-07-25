"""The ``lofi`` generative style — Strudel source parameterized by activity.

Deterministic: the same ``(signal, intensity, band, fade_ms)`` always renders
byte-identical text, so it is golden-file testable. The activity maps to music
as follows:

- **intensity** opens the low-pass filter, lifts the tempo, and closes the
  reverb (calm/theta = dark & spacious; energetic/gamma = bright & tight);
- **volume** picks the drum busyness;
- **participant_count** adds layers — a lead appears with ≥2, extra percussion
  with ≥4 — and each participant's assigned voice seeds the lead timbre;
- **volatility** sets how many notes the lead scatters across the bar;
- **themes** nudge which chord progression is chosen.

All layers ``fadeIn`` so pieces enter gently between polls (plan §5.3).
"""

from __future__ import annotations

from ...core.models import ActivitySignal
from ..brainwave import clamp01

# Jazzy ii–V–I-ish loops; one is chosen deterministically from the signal.
_PROGRESSIONS = (
    "<Cm7 Am7 Fmaj7 G7>",
    "<Am9 Dm7 G9 Cmaj7>",
    "<Em7 Cmaj7 Am7 B7>",
    "<Dm7 G9 Cmaj7 Am7>",
)
_BASS_ROOTS = (
    "<c2 a1 f1 g1>",
    "<a1 d2 g1 c2>",
    "<e2 c2 a1 b1>",
    "<d2 g1 c2 a1>",
)
# Synth kick patterns (a low sine blip), sparse to busy by intensity.
_KICKS = (
    "c1 ~ ~ ~",
    "c1 ~ c1 ~",
    "c1 ~ c1 ~",
    "c1 ~ c1 [~ c1]",
)
# A pentatonic-ish lead phrase per number of scattered notes (2..5).
_LEAD_STEPS = {
    2: "0 ~ ~ 4 ~ ~ 7 ~",
    3: "0 ~ 4 ~ 7 ~ 4 ~",
    4: "0 4 7 ~ 9 7 4 ~",
    5: "0 4 7 9 7 4 2 0",
}


def _seed(signal: ActivitySignal) -> int:
    """A small, deterministic, content-derived selector."""
    theme_bits = sum(ord(c) for c in "".join(signal.themes)[:12])
    return signal.volume * 7 + signal.participant_count * 13 + int(signal.volatility * 100) + theme_bits


def render(
    signal: ActivitySignal,
    intensity: float,
    band: str,
    fade_ms: int = 2000,
) -> str:
    """Render a lofi Strudel program from the signal and derived energy."""
    intensity = clamp01(intensity)
    # Tempo rides on the pattern via .fast() — @strudel/web has no setcps.
    # ~0.6x (theta, laid-back) to ~1.5x (gamma, driving).
    fast = round(0.6 + intensity * 0.9, 2)
    lpf = round(400 + intensity * 1600)  # darker when calm, brighter when busy
    verb = round(0.5 + (1.0 - intensity) * 0.4, 2)  # reverb wash: lusher when calm

    variant = _seed(signal) % len(_PROGRESSIONS)
    prog = _PROGRESSIONS[variant]
    bass = _BASS_ROOTS[variant]
    kick = _KICKS[min(3, int(intensity * 4))]

    # The whole beat is SYNTHESIZED (no samples): a sine-blip kick, a white-noise
    # snare on the backbeat, and filtered white-noise hats — so a lofi beat plays
    # immediately and reliably, even before/without any sample download.
    note_count = 2 + min(3, round(clamp01(signal.volatility) * 3))
    steps = _LEAD_STEPS[note_count]
    lead_voice = next(iter(signal.actor_voices.values()), "rhodes")
    lead_lpf = min(3000, lpf + 600)
    hats = 8 if intensity >= 0.5 else 4

    # Melody carries lush reverb tails — the incidental mid/high harmonics.
    melody = (
        f'  n("{steps}").scale("C:minor:pentatonic").s("triangle")'
        f".decay(0.2).sustain(0).lpf({lead_lpf}).room({round(verb - 0.1, 2)})"
        ".roomsize(4).gain(0.28)"
    )
    # Ambient pad — a big reverb wash sets the "room".
    pad = (
        f'  chord("{prog}").voicing().s("sawtooth").lpf({lpf}).room({verb})'
        ".roomsize(5).gain(0.32).slow(2)"
    )
    layers = [
        pad,
        # Bass stays dry and low so the low end doesn't turn to mud.
        f'  note("{bass}").s("sawtooth").lpf(260).gain(0.38).slow(2)',
        melody,
        # The beat sits softly under the wash — present, not driving.
        f'  note("{kick}").s("sine").decay(0.16).sustain(0).gain(0.6)',
        '  s("white").struct("~ x ~ x").decay(0.07).sustain(0).room(0.3).gain(0.18)',
        # Airy hats with their own reverb shimmer (high frequencies).
        f'  s("white*{hats}").decay(0.02).sustain(0).hpf(9000).room(0.45).roomsize(3).gain(0.16)',
    ]

    # Metadata lives only in the header comment — never inline, so it can't eat
    # the comma that separates stacked layers.
    lead_note = f", lead {lead_voice}" if lead_voice else ""
    header = (
        f"// maelcom lofi · band={band} · intensity={round(intensity, 3)} · "
        f"{signal.volume} change{'s' if signal.volume != 1 else ''}, "
        f"{signal.participant_count} voice{'s' if signal.participant_count != 1 else ''}"
        f"{lead_note}"
    )
    body = ",\n".join(layers)
    return f"{header}\nstack(\n{body}\n).fast({fast})"
