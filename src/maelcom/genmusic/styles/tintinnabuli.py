"""The ``tintinnabuli`` style — Arvo Pärt's method, largo, modified-piano voices.

Two voices move together (Pärt's tintinnabuli technique):

- **M-voice** (melodic): a stepwise diatonic line that develops over four bars.
- **T-voice** (tintinnabuli): sounds only tonic-triad tones, shadowing the
  M-voice — alternating the nearest triad note *below* (inferior) and *above*
  (superior) it from note to note.

They sit over a deep root drone, with a sparse-to-busy, harmonically rich
sawtooth lead whose density tracks activity. Everything is **largo** and biased
to whole and quarter notes; the voices evolve as the ``ActivitySignal`` changes.

Voices use a **modified-piano synth** — a *filtered sawtooth* with a piano-like
envelope. (@strudel/web has no piano soundfont loaded, and per the project's
timbre rules triangle/square are reserved for low, short sounds — so a filtered
sawtooth is the piano stand-in for these mid-register, sustained notes.)
"""

from __future__ import annotations

from ...core.models import ActivitySignal
from ..brainwave import clamp01

_KEY = "A"  # A minor tonic — a classic Pärt key
_TRIAD = (0, 2, 4)  # scale degrees of the tonic triad (root, third, fifth)

# Modified-piano synth: a sawtooth with a piano-like amplitude ADSR *and* a
# filter envelope (bright attack decaying to a mellow body), which shapes the
# tone of these >C3 notes so they don't sit static/harsh. No samples; no
# triangle/square in this register per the timbre rules.
_PIANO = (
    's("sawtooth").lpf(900).lpenv(2.5).lpa(0.005).lpd(0.4).lps(0.15).lpr(0.4)'
    ".attack(0.004).decay(0.4).sustain(0.08).release(0.6)"
)


def _seed(signal: ActivitySignal) -> int:
    theme_bits = sum(ord(c) for c in "".join(signal.themes)[:12])
    return (
        signal.volume * 7
        + signal.participant_count * 13
        + int(signal.volatility * 100)
        + theme_bits
    )


def _m_voice(signal: ActivitySignal, n: int = 16) -> list[int]:
    """A developing, mostly stepwise melodic line of scale degrees (the M-voice)
    — deterministic, evolving with the signal, spanning ~1.5 octaves."""
    seed = _seed(signal)
    steps = (-1, 0, 1, 0, -1, 1, 0, 2, -2, 1)
    d = 2 + (seed % 4)
    out = [d]
    for i in range(1, n):
        d = max(0, min(11, d + steps[(seed >> (i * 2)) % len(steps)]))
        out.append(d)
    return out


def _t_below(m: int) -> int:
    d = m
    while d % 7 not in _TRIAD:
        d -= 1
    return d


def _t_above(m: int) -> int:
    d = m
    while d % 7 not in _TRIAD:
        d += 1
    return d


def _t_voice(m: list[int]) -> list[int]:
    """The tintinnabuli shadow: nearest triad tone, alternating inferior (below)
    and superior (above) from note to note."""
    return [(_t_above(x) if i % 2 else _t_below(x)) for i, x in enumerate(m)]


def _bars(degrees: list[int]) -> str:
    """Format degrees as bars of four quarter notes: ``<[..] [..] ...>`` — the
    whole-cycle alternation keeps note values to quarters and phrases to bars."""
    bars = [" ".join(str(x) for x in degrees[i : i + 4]) for i in range(0, len(degrees), 4)]
    return "<[" + "] [".join(bars) + "]>"


def _lead(signal: ActivitySignal, intensity: float) -> str:
    """A sparse-to-busy lead phrase (an octave above the M-voice), denser with
    higher intensity."""
    hi = [x + 7 for x in _m_voice(signal, 8)]
    if intensity < 0.4:
        return f"<{hi[0]} ~ {hi[3]} ~>"
    if intensity < 0.7:
        return f"<[{hi[0]} ~ {hi[1]} ~] [~ {hi[2]} ~ {hi[3]}]>"
    return f"<[{hi[0]} {hi[1]} ~ {hi[2]}] [{hi[3]} ~ {hi[4]} {hi[5]}]>"


def _arp(signal: ActivitySignal) -> str:
    """A minimalist quartal/quintal arpeggio cell (Glass/Bach), 8 eighth-notes in
    4/4, emphasizing 4ths (0->3) and 5ths (0->4)."""
    cells = (
        "0 3 4 7 4 3 4 0",
        "0 4 7 4 3 7 4 0",
        "0 3 7 3 4 0 4 3",
        "4 7 4 3 0 3 4 7",
    )
    return cells[_seed(signal) % len(cells)]


def render(signal: ActivitySignal, intensity: float, band: str, fade_ms: int = 2000) -> str:
    intensity = clamp01(intensity)
    m = _m_voice(signal, 16)
    t = _t_voice(m)
    lead_lpf = round(1700 + intensity * 1300)
    verb = round(0.55 + (1.0 - intensity) * 0.3, 2)  # lusher reverb when calm
    tension = clamp01((signal.volume - 4) / 24.0)  # a burst of news events → dissonance
    pad_lfo = "sine.range(500,1200).slow(8)"  # slow analog filter breathing
    arp_lfo = "sine.range(700,1300).slow(6)"

    lead_layer = (
        f'  n("{_lead(signal, intensity)}").scale("{_KEY}4:minor").s("sawtooth").detune(0.1)'
        f".lpf({lead_lpf}).lpenv(2).lpa(0.01).lpd(0.4).lps(0.2).lpr(0.5).room(0.6).roomsize(4).gain(0.16)"
    )
    # Above-C3 softening: white noise as *part of each high note* — a breathy
    # transient on the melody's attack, following the note's own ADSR (short,
    # lightly reverbed), not a constant background drone.
    air = (
        f'  n("{_bars(m)}").scale("{_KEY}3:minor").s("white").hpf(1500)'
        ".attack(0.004).decay(0.28).sustain(0.03).release(0.35).room(0.3).roomsize(2).gain(0.2)"
    )
    # Quartal pad (root/4th/5th) with slow analog filter breathing — whole notes.
    pad = (
        f'  note("<[a2,d3,e3] [a2,e3,a3]>").s("sawtooth").detune(0.1).lpf({pad_lfo})'
        f".room({verb}).roomsize(6).gain(0.22)"
    )
    # Minimalist arpeggio ostinato (Glass/Bach) in 4ths and 5ths — eighths, 4/4.
    arp = (
        f'  n("{_arp(signal)}").scale("{_KEY}2:minor").s("sawtooth").detune(0.12).lpf({arp_lfo})'
        f".room(0.6).roomsize(4).gain(0.18)"
    )
    layers = [
        pad,
        arp,
        # M-voice — the singing tintinnabuli melody (modified piano, quarter notes).
        f'  n("{_bars(m)}").scale("{_KEY}3:minor").{_PIANO}.detune(0.07).room({verb}).roomsize(4).gain(0.4)',
        # T-voice — the tintinnabuli triad shadow (alternating below/above).
        f'  n("{_bars(t)}").scale("{_KEY}3:minor").{_PIANO}.detune(0.07).room({verb}).roomsize(4).gain(0.28)',
        # Deep root drone (root + fifth), whole notes.
        f'  note("<{_KEY.lower()}1 e1>").s("sine").lpf(400).gain(0.28)',
        air,
        # Sparse, soft high lead (Field/nocturne feel).
        lead_layer,
    ]
    # Consonant by default; a gentle Satie-style dissonance (add9: A + B) enters
    # as an accent that grows with a burst of news events.
    if tension > 0.05:
        stab_gain = round(0.04 + tension * 0.2, 2)
        layers.append(
            f'  note("<[a3,b4] ~ ~ ~>").s("sawtooth").lpf(1400)'
            f".room(0.7).roomsize(4).gain({stab_gain})"
        )
    header = (
        f"// maelcom tintinnabuli (largo) · band={band} · "
        f"{signal.volume} change{'s' if signal.volume != 1 else ''}, "
        f"{signal.participant_count} voice{'s' if signal.participant_count != 1 else ''}"
    )
    body = ",\n".join(layers)
    return f"{header}\nstack(\n{body}\n).slow(2)"  # largo
