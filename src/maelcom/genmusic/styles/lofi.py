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
# Drum patterns from sparse to busy, indexed by intensity.
_DRUMS = (
    "bd ~ ~ ~",
    "bd ~ [~ sd] ~",
    "bd ~ sd ~, ~ ~ [~ bd] ~",
    "bd*2 ~ sd ~, ~ [~ bd] sd ~",
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
    cps = round(0.30 + intensity * 0.45, 3)  # 0.30 (theta) .. 0.75 (gamma)
    lpf = round(400 + intensity * 1600)  # darker when calm, brighter when busy
    room = round(max(0.2, 0.6 - intensity * 0.3), 2)  # spacious when calm
    fade_s = round(fade_ms / 1000.0, 3)

    variant = _seed(signal) % len(_PROGRESSIONS)
    prog = _PROGRESSIONS[variant]
    bass = _BASS_ROOTS[variant]
    drums = _DRUMS[min(3, int(intensity * 4))]

    layers = [
        f'  chord("{prog}").voicing().s("sawtooth").lpf({lpf}).room({room}).gain(0.5).slow(2)',
        f'  note("{bass}").s("sawtooth").lpf(300).gain(0.55).slow(2)',
        f'  s("{drums}").bank("RolandTR909").gain(0.7)',
        '  s("vinyl").gain(0.25)',  # constant lofi crackle
    ]

    # A lead appears once ≥2 people are active; its density tracks volatility,
    # its timbre the busiest participant's assigned voice.
    lead_voice = None
    if signal.participant_count >= 2:
        note_count = 2 + min(3, round(clamp01(signal.volatility) * 3))
        steps = _LEAD_STEPS[note_count]
        lead_voice = next(iter(signal.actor_voices.values()), "rhodes")
        lead_lpf = min(3000, lpf + 600)
        layers.insert(
            1,
            f'  n("{steps}").scale("C:minor:pentatonic").s("triangle").lpf({lead_lpf}).gain(0.4)',
        )
    # A busy room (≥4 people) gets extra hats.
    if signal.participant_count >= 4:
        layers.append(f'  s("hh*{4 if intensity < 0.6 else 8}").gain(0.3)')

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
    return f"{header}\nsetcps({cps})\nstack(\n{body}\n).fadeIn({fade_s})"
