"""The ``tintinnabuli`` style — Arvo Pärt's method, largo, modified-piano voices.

Two voices move together (Pärt's tintinnabuli technique):

- **M-voice** (melodic): a stepwise diatonic line around the tonic.
- **T-voice** (tintinnabuli): sounds only the tonic-triad tones, shadowing the
  M-voice at the nearest triad note at or below it.

They sit over a deep root drone, with a sparse, harmonically rich sawtooth lead.
Everything is **largo** and biased to whole and quarter notes. Voices are
generated deterministically from the ``ActivitySignal`` and evolve as it changes.
Piano is a *modified-piano synth* (a triangle with a piano-like envelope), since
@strudel/web has no piano samples loaded.
"""

from __future__ import annotations

from ...core.models import ActivitySignal
from ..brainwave import clamp01

_KEY = "A"  # A minor tonic — a classic Pärt key
_TRIAD = (0, 2, 4)  # scale degrees of the tonic triad (root, third, fifth)

# Modified-piano synth (no samples): a mellow triangle with a piano-like ADSR.
_PIANO = 's("triangle").attack(0.004).decay(0.4).sustain(0.08).release(0.6)'


def _seed(signal: ActivitySignal) -> int:
    theme_bits = sum(ord(c) for c in "".join(signal.themes)[:12])
    return (
        signal.volume * 7
        + signal.participant_count * 13
        + int(signal.volatility * 100)
        + theme_bits
    )


def _m_voice(signal: ActivitySignal, n: int = 8) -> list[int]:
    """A stepwise melodic line of scale degrees (the M-voice) — deterministic,
    mostly stepwise motion, evolving with the signal."""
    seed = _seed(signal)
    steps = (-1, 0, 1, 0, -1, 1, 0, 2)
    d = 2 + (seed % 4)
    out = [d]
    for i in range(1, n):
        d = max(0, min(9, d + steps[(seed >> (i * 3)) % len(steps)]))
        out.append(d)
    return out


def _t_below(m: int) -> int:
    """Tintinnabuli T-voice: nearest tonic-triad scale degree at or below ``m``
    (triad tones are the scale degrees congruent to 0, 2 or 4 mod 7)."""
    d = m
    while d % 7 not in _TRIAD:
        d -= 1
    return d


def _bars(degrees: list[int]) -> str:
    """Two bars of four quarter notes: ``<[a b c d] [e f g h]>`` (whole-cycle
    alternation keeps the note values to quarters and the phrase to two bars)."""
    first = " ".join(str(x) for x in degrees[:4])
    second = " ".join(str(x) for x in degrees[4:8])
    return f"<[{first}] [{second}]>"


def render(signal: ActivitySignal, intensity: float, band: str, fade_ms: int = 2000) -> str:
    intensity = clamp01(intensity)
    m = _m_voice(signal, 8)
    t = [_t_below(x) for x in m]
    lead = f"<{m[0] + 7} ~ {m[3] + 7} ~>"  # sparse, an octave up
    lead_lpf = round(1800 + intensity * 1600)  # brighter (more harmonics) when busy
    verb = round(0.5 + (1.0 - intensity) * 0.3, 2)  # lusher reverb when calm
    scale = f"{_KEY}3:minor"

    # Synth lead — high harmonic content (sawtooth), sparse and high.
    lead_layer = (
        f'  n("{lead}").scale("{_KEY}4:minor").s("sawtooth").lpf({lead_lpf})'
        ".room(0.5).roomsize(3).gain(0.2)"
    )
    layers = [
        # M-voice — stepwise piano melody in quarter notes.
        f'  n("{_bars(m)}").scale("{scale}").{_PIANO}.room({verb}).roomsize(4).gain(0.5)',
        # T-voice — the tintinnabuli triad shadow, softer.
        f'  n("{_bars(t)}").scale("{scale}").{_PIANO}.room({verb}).roomsize(4).gain(0.36)',
        # Root drone — deep whole notes.
        f'  note("<{_KEY.lower()}1 e1>").s("sine").lpf(500).gain(0.32)',
        lead_layer,
    ]
    header = (
        f"// maelcom tintinnabuli (largo) · band={band} · "
        f"{signal.volume} change{'s' if signal.volume != 1 else ''}, "
        f"{signal.participant_count} voice{'s' if signal.participant_count != 1 else ''}"
    )
    body = ",\n".join(layers)
    return f"{header}\nstack(\n{body}\n).slow(2)"  # largo
