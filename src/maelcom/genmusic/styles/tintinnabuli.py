"""The ``tintinnabuli`` style — Arvo Pärt's method, largo, modified-piano voices.

Two voices move together (Pärt's tintinnabuli technique):

- **M-voice** (melodic): a stepwise diatonic line that develops over four bars.
- **T-voice** (tintinnabuli): sounds only tonic-triad tones, shadowing the
  M-voice — alternating the nearest triad note *below* (inferior) and *above*
  (superior) it from note to note.

They sit over a deep root drone, with a sparse-to-busy, harmonically rich
sawtooth lead whose density tracks activity. Everything is **largo** and biased
to whole and quarter notes; the voices evolve as the ``ActivitySignal`` changes.

The piece is arranged over time (see :mod:`maelcom.genmusic.arrange`): 16-bar
sections joined by 4-bar circle-of-fifths transitions, each transition spelling
out the current chord, a quartal pivot chord, then the new chord before the
voices settle into the new key; after ~120 bars, earlier sections repeat.

Voices use a **modified-piano synth** — a *filtered sawtooth* with a piano-like
envelope. (@strudel/web has no piano soundfont loaded, and per the project's
timbre rules triangle/square are reserved for low, short sounds — so a filtered
sawtooth is the piano stand-in for these mid-register, sustained notes.)
"""

from __future__ import annotations

from ...core.models import ActivitySignal
from ..arrange import SECTION_BARS, TRANSITION_BARS, build_plan, common_tone
from ..brainwave import clamp01

_KEY = "A"  # A minor tonic — a classic Pärt key
_TRIAD = (0, 2, 4)  # scale degrees of the tonic triad (root, third, fifth)

# Voicing tables for the keys in the circle-of-fifths window (see arrange.py).
# The perfect 4th and 5th above each tonic (quartal pad + drone note names)…
_FOURTH_OF = {"G": "c", "D": "g", "A": "d", "E": "a", "B": "e"}
_FIFTH_OF = {"G": "d", "D": "a", "A": "e", "E": "b", "B": "f#"}
# …and the major 9th above (the gentle add9 dissonance tone).
_NINTH_OF = {"G": "a", "D": "e", "A": "b", "E": "f#", "B": "c#"}

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


def _m_voice(signal: ActivitySignal, n: int = 16, salt: int = 0) -> list[int]:
    """A developing, mostly stepwise melodic line of scale degrees (the M-voice)
    — deterministic, evolving with the signal, spanning ~1.5 octaves. ``salt``
    varies the line per arrangement section (the same salt reproduces it)."""
    seed = _seed(signal) + salt
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


def _lead(signal: ActivitySignal, intensity: float, salt: int = 0) -> str:
    """A sparse-to-busy lead phrase (an octave above the M-voice), denser with
    higher intensity."""
    hi = [x + 7 for x in _m_voice(signal, 8, salt)]
    if intensity < 0.4:
        return f"<{hi[0]} ~ {hi[3]} ~>"
    if intensity < 0.7:
        return f"<[{hi[0]} ~ {hi[1]} ~] [~ {hi[2]} ~ {hi[3]}]>"
    return f"<[{hi[0]} {hi[1]} ~ {hi[2]}] [{hi[3]} ~ {hi[4]} {hi[5]}]>"


def _arp(signal: ActivitySignal, salt: int = 0) -> str:
    """A minimalist quartal/quintal arpeggio cell (Glass/Bach), 8 eighth-notes in
    4/4, emphasizing 4ths (0->3) and 5ths (0->4)."""
    cells = (
        "0 3 4 7 4 3 4 0",
        "0 4 7 4 3 7 4 0",
        "0 3 7 3 4 0 4 3",
        "4 7 4 3 0 3 4 7",
    )
    return cells[(_seed(signal) + salt) % len(cells)]


def _section(
    signal: ActivitySignal,
    key: str,
    salt: int,
    intensity: float,
    tension: float,
    verb: float,
) -> str:
    """One 16-bar section in ``key``: the full tintinnabuli texture (quartal pad,
    minimalist arpeggio, M/T voices, drone, high-note softening, sparse lead, and
    a gentle add9 accent on news bursts), transposed by scale tonic and voicing
    tables. The melody ``salt`` evolves the line from section to section."""
    m = _m_voice(signal, 16, salt)
    t = _t_voice(m)
    bars_m, bars_t = _bars(m), _bars(t)
    lo = key.lower()
    fourth, fifth, ninth = _FOURTH_OF[key], _FIFTH_OF[key], _NINTH_OF[key]
    lead_lpf = round(1700 + intensity * 1300)

    pad = (
        f'    note("<[{lo}2,{fourth}3,{fifth}3] [{lo}2,{fifth}3,{lo}3]>").s("sawtooth")'
        f'.detune(0.1).lpf(sine.range(500,1200).slow(8)).room({verb}).roomsize(6).gain(0.22)'
    )
    arp = (
        f'    n("{_arp(signal, salt)}").scale("{key}2:minor").s("sawtooth").detune(0.12)'
        f'.lpf(sine.range(700,1300).slow(6)).room(0.6).roomsize(4).gain(0.18)'
    )
    mvoice = (
        f'    n("{bars_m}").scale("{key}3:minor").{_PIANO}'
        f'.detune(0.07).room({verb}).roomsize(4).gain(0.4)'
    )
    tvoice = (
        f'    n("{bars_t}").scale("{key}3:minor").{_PIANO}'
        f'.detune(0.07).room({verb}).roomsize(4).gain(0.28)'
    )
    drone = f'    note("<{lo}1 {fifth}1>").s("sine").lpf(400).gain(0.28)'
    air = (
        f'    n("{bars_m}").scale("{key}3:minor").s("white").hpf(1500)'
        f'.attack(0.004).decay(0.28).sustain(0.03).release(0.35).room(0.3).roomsize(2).gain(0.2)'
    )
    lead = (
        f'    n("{_lead(signal, intensity, salt)}").scale("{key}4:minor").s("sawtooth").detune(0.1)'
        f'.lpf({lead_lpf}).lpenv(2).lpa(0.01).lpd(0.4).lps(0.2).lpr(0.5).room(0.6).roomsize(4).gain(0.16)'
    )
    layers = [pad, arp, mvoice, tvoice, drone, air, lead]
    # Consonant by default; a gentle Satie-style add9 (root + 9th) accent enters
    # with a burst of news events.
    if tension > 0.05:
        stab_gain = round(0.04 + tension * 0.2, 2)
        layers.append(
            f'    note("<[{lo}3,{ninth}4] ~ ~ ~>").s("sawtooth").lpf(1400)'
            f".room(0.7).roomsize(4).gain({stab_gain})"
        )
    body = ",\n".join(layers)
    return f"stack(\n{body}\n  )"


# The quartal arpeggio cell used to spell out each chord across a transition.
_TRANSITION_ARP = "0 3 4 7 4 3 4 0"


def _transition(k0: str, k1: str, verb: float) -> str:
    """A 4-bar bridge from ``k0`` to ``k1``: three arpeggios — the current chord,
    a quartal *pivot* chord on the common tone (shared with both keys, all 4ths
    and 5ths), then the new chord — voiced as a scale journey
    ``<k0 pivot pivot k1>`` so one bar each opens and closes and the pivot spans
    the middle. Thinner than a section, so the modulation reads as a breath."""
    pivot = common_tone(k0, k1)
    j3 = f'"<{k0}3:minor {pivot}3:minor {pivot}3:minor {k1}3:minor>"'
    j2 = f'"<{k0}2:minor {pivot}2:minor {pivot}2:minor {k1}2:minor>"'
    arp = (
        f'    n("{_TRANSITION_ARP}").scale({j3}).s("sawtooth").detune(0.12)'
        f'.lpf(sine.range(700,1300).slow(4)).room(0.6).roomsize(4).gain(0.24)'
    )
    # A soft quartal pad (root/4th/5th as scale degrees 0/3/4) drifting the keys.
    pad = (
        f'    n("<[0,3,4] [0,4,7]>").scale({j2}).s("sawtooth").detune(0.1)'
        f'.lpf(sine.range(500,1100).slow(4)).room({verb}).roomsize(6).gain(0.18)'
    )
    return f"stack(\n{arp},\n{pad}\n  )"


def render(signal: ActivitySignal, intensity: float, band: str, fade_ms: int = 2000) -> str:
    intensity = clamp01(intensity)
    verb = round(0.55 + (1.0 - intensity) * 0.3, 2)  # lusher reverb when calm
    tension = clamp01((signal.volume - 4) / 24.0)  # a burst of news events → dissonance

    plan = build_plan(_seed(signal))
    blocks: list[str] = []
    for unit in plan:
        section = _section(signal, unit["key"], unit["salt"], intensity, tension, verb)
        transition = _transition(unit["key"], unit["next"], verb)
        blocks.append(f"  [{SECTION_BARS}, {section}]")
        blocks.append(f"  [{TRANSITION_BARS}, {transition}]")

    keys = " ".join(u["key"] for u in plan)
    header = (
        f"// maelcom tintinnabuli (largo, 4/4) · band={band} · "
        f"{len(plan)} sections [{keys}] · "
        f"{signal.volume} change{'s' if signal.volume != 1 else ''}, "
        f"{signal.participant_count} voice{'s' if signal.participant_count != 1 else ''}"
    )
    body = ",\n".join(blocks)
    return f"{header}\narrange(\n{body}\n).slow(2)"  # largo
