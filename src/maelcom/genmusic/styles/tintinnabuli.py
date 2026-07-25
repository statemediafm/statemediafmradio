"""The default generative style — dark, low, spacious canon & call-response.

This module is built to an explicit **composition rule base** — see the ``RULES``
tuple below, which is the single source of truth we build up from. In short:
confined to **G Dorian**, everything **low** (octave 1) and **low-passed**; the
texture is mainly **canons and call-and-response** with double-time melodies that
come and go; the rhythm evolves every 64 bars (turnaround / pause / drop); a brief
**tintinnabuli** (Pärt M/T) passage recurs ~every 180 bars; no drums for now.

Voices use a dark **modified-piano synth** — a heavily filtered sawtooth with a
soft envelope (no piano soundfont is loaded in @strudel/web). The circle-of-fifths
modulation in :mod:`maelcom.genmusic.arrange` is dormant while we're in one key.
"""

from __future__ import annotations

from ...core.models import ActivitySignal
from ..brainwave import clamp01

_KEY = "G"  # confined to G for now
_MODE = "dorian"  # both voices in the Dorian mode (no minor keys) until further notice
_TRIAD = (0, 2, 4)  # scale degrees of the tonic triad (root, third, fifth)
_TOP = 7  # melodic ceiling in scale degrees (G1..G2) — keep everything low

# Dark modified-piano synth: a low-passed sawtooth with a soft amplitude ADSR and
# a gentle filter envelope. Cutoff stays low so nothing bites.
_PIANO = (
    's("sawtooth").lpf(480).lpenv(1.2).lpa(0.01).lpd(0.5).lps(0.3).lpr(0.6)'
    ".attack(0.008).decay(0.5).sustain(0.12).release(0.8)"
)


def _seed(signal: ActivitySignal) -> int:
    theme_bits = sum(ord(c) for c in "".join(signal.themes)[:12])
    return (
        signal.volume * 7
        + signal.participant_count * 13
        + int(signal.volatility * 100)
        + theme_bits
    )


def _rng(n: int) -> int:
    """A small integer hash — a well-mixed pseudo-random value per index (the old
    ``seed >> 2*i`` ran out of bits and pinned the melody to one note)."""
    x = (n * 2654435761 + 1013904223) & 0xFFFFFFFF
    x ^= x >> 16
    x = (x * 2246822519) & 0xFFFFFFFF
    x ^= x >> 13
    return x


def _m_voice(signal: ActivitySignal, n: int = 32, salt: int = 0) -> list[int]:
    """A developing, mostly stepwise melodic line of scale degrees (the M-voice),
    kept within a single low octave (``0.._TOP``). ``salt`` varies the line."""
    seed = _seed(signal) + salt
    steps = (-2, -1, -1, 0, 1, 1, 2, -1, 1, 0)
    d = 2 + (_rng(seed) % 4)  # start mid-low (2..5)
    out = [d]
    for i in range(1, n):
        step = steps[_rng(seed * 31 + i * 7) % len(steps)]
        nd = d + step
        if nd < 0 or nd > _TOP:
            nd = d - step  # reflect at the octave bounds instead of pinning
        d = nd
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


def _bars(degrees: list[int], gate: tuple[bool, ...], sparse: bool = True) -> str:
    """Format degrees as gated bars of quarter notes: silent bars become a rest,
    and (when ``sparse``) each sounding bar keeps only beats 1 and 3 — airy, so
    the voice floats rather than plods. ``<[..] [..] ...>`` = one bar per cycle."""
    out: list[str] = []
    for b, i in enumerate(range(0, len(degrees), 4)):
        if b >= len(gate) or not gate[b]:
            out.append("~")
            continue
        four = degrees[i : i + 4]
        if sparse:
            a = four[0] if len(four) > 0 else "~"
            c = four[2] if len(four) > 2 else "~"
            out.append(f"{a} ~ {c} ~")
        else:
            out.append(" ".join(str(x) for x in four))
    return "<[" + "] [".join(out) + "]>"


# ── The composition rule base ──────────────────────────────────────────────
# Consolidated from the running direction; the single source of truth. We build
# up from here. Each rule is enforced by the code below (noted in parentheses).
RULES: tuple[str, ...] = (
    "1. Confined to G Dorian — no minor, no major keys; every voice shares the key (consonant).",
    "2. (Dormant) modulate only to adjacent / consonant circle-of-fifths keys.",
    "3. Everything low — octave 1 (~49-98 Hz); nothing high.",
    "4. A low-pass on every voice (no cutoff above ~500 Hz) — nothing harsh.",
    "5. 4/4, largo (.slow(2)); it should float, not plod.",
    "6. Mainly canons and call-and-response; voices come and go and trade off.",
    "7. Melodies run double-time or faster; consonant with the other voices.",
    "8. Evolve by fractal branching, but only 1-2 branches (a leader + at most two derived voices).",
    "9. No ostinato longer than 16 bars; keep the material varying.",
    "10. Evolve the rhythm every 64 bars with a turnaround, a pause, and a drop.",
    "11. Tintinnabuli (M/T voices) only briefly, ~every 180 bars.",
    "12. No drums / percussion for now.",
    "13. A burst of news swells the tonic triad — a consonant emphasis.",
    "14. Deterministic: the same signal always renders the same music.",
)

_SCALE = f"{_KEY}1:{_MODE}"  # rules 1 & 3: G Dorian, low octave
_CALL = (True, True, False, False, True, True, False, False)  # leader sings…
_RESP = (False, False, True, True, False, False, True, True)  # …responder answers in the gaps


def _fast(intensity: float) -> int:
    """Melodic speed — double-time or greater with activity (rule 7)."""
    return 2 + round(clamp01(intensity) * 2)


def _bed(verb: float) -> list[str]:
    """The sustained low ground (rules 3-5): a held root/fifth drone and a slow
    quartal pad, both deep and low-passed, so the harmony floats without a pulse."""
    drone = f'    n("<[0,4]>").scale("{_SCALE}").s("sine").attack(0.4).release(1.6).lpf(150).gain(0.3)'
    pad = (
        f'    n("<[0,3,4] [0,4,7]>").scale("{_SCALE}").s("sawtooth").detune(0.08)'
        f".lpf(sine.range(220,480).slow(8)).room({verb}).roomsize(8).gain(0.16)"
    )
    return [drone, pad]


def _voice(bars_str: str, fast: int, verb: float, gain: float, extra: str = "") -> str:
    """A dark-piano melodic voice (rules 3-5, 7): low, low-passed, double-time."""
    return (
        f'    n("{bars_str}").scale("{_SCALE}").{_PIANO}.detune(0.06).fast({fast})'
        f"{extra}.room({verb}).roomsize(7).gain({gain})"
    )


def _stack(layers: list[str]) -> str:
    return "stack(\n" + ",\n".join(layers) + "\n  )"


def _split(span: int, chunk: int = 14) -> list[int]:
    """Break a span into pieces of at most ``chunk`` bars, so no single phrase
    (ostinato) persists for more than 16 bars (rule 9)."""
    sizes: list[int] = []
    while span > 0:
        sizes.append(min(chunk, span))
        span -= sizes[-1]
    return sizes


def _canon_chunk(signal: ActivitySignal, intensity: float, verb: float, tension: float, salt: int) -> str:
    """Canon + call-and-response (rules 6-8): a leader with at most two branches —
    a canon follower a bar later, and a response answering in the leader's gaps."""
    fast = _fast(intensity)
    line = _m_voice(signal, 32, salt)
    lead = _bars(line, _CALL, sparse=False)
    resp = _bars(line, _RESP, sparse=False)
    layers = _bed(verb)
    layers.append(_voice(lead, fast, verb, 0.32))  # leader (call)
    layers.append(_voice(lead, fast, verb, 0.2, extra=".late(1)"))  # branch 1: canon
    layers.append(_voice(resp, fast, verb, 0.26))  # branch 2: response
    if tension > 0.05:  # rule 13
        g = round(0.05 + tension * 0.15, 2)
        layers.append(
            f'    n("<[0,2,4] ~ ~ ~>").scale("{_SCALE}").s("sawtooth")'
            f".lpf(400).attack(0.05).release(1.2).room(0.8).roomsize(8).gain({g})"
        )
    return _stack(layers)


def _canon_body(signal: ActivitySignal, intensity: float, verb: float, tension: float, span: int, base: int) -> str:
    """A canon/call-response movement body of ``span`` bars, its material varying
    every <=14 bars so no ostinato outstays 16 bars (rule 9)."""
    parts = [
        f"[{sz}, {_canon_chunk(signal, intensity, verb, tension, base + 1 + k * 13)}]"
        for k, sz in enumerate(_split(span))
    ]
    return "arrange(" + ", ".join(parts) + ")"


def _turnaround(intensity: float, verb: float) -> str:
    """A descending quartal turnaround resolving to the tonic (rule 10)."""
    return _stack([*_bed(verb), _voice("<[7 4 3 0] [4 2 0 ~]>", _fast(intensity), verb, 0.3)])


def _pause(verb: float) -> str:
    """A breath — the bed thins to a lingering root (rule 10)."""
    return _stack(
        [f'    n("0").scale("{_SCALE}").s("sine").attack(0.6).release(2.6).lpf(120).gain(0.2)']
    )


def _drop(signal: ActivitySignal, intensity: float, verb: float) -> str:
    """The drop — a deep sub-root and the canon crashing back in (rule 10)."""
    fast = _fast(intensity)
    lead = _bars(_m_voice(signal, 16, salt=3), (True, True, True, True), sparse=False)
    return _stack(
        [
            *_bed(verb),
            f'    n("<0 ~ ~ ~>").scale("{_SCALE}").s("sine").lpf(110).attack(0.002).decay(0.6).sustain(0).gain(0.34)',
            _voice(lead, fast, verb, 0.32),
            _voice(lead, fast, verb, 0.2, extra=".late(0.5)"),
        ]
    )


def _tint_passage(signal: ActivitySignal, intensity: float, verb: float) -> str:
    """The rare tintinnabuli passage — M-voice + T-voice shadow (rule 11)."""
    fast = _fast(intensity)
    m = _m_voice(signal, 16, salt=7)
    t = _t_voice(m)
    full = (True, True, True, True)
    return _stack(
        [
            *_bed(verb),
            _voice(_bars(m, full, sparse=False), fast, verb, 0.32),
            _voice(_bars(t, full, sparse=False), fast, verb, 0.26),
        ]
    )


def render(signal: ActivitySignal, intensity: float, band: str, fade_ms: int = 2000) -> str:
    intensity = clamp01(intensity)
    verb = round(0.6 + (1.0 - intensity) * 0.25, 2)  # lusher reverb when calm
    tension = clamp01((signal.volume - 4) / 24.0)  # a burst of news events → swell (rule 13)

    turn, pause, drop = _turnaround(intensity, verb), _pause(verb), _drop(signal, intensity, verb)
    # Three ~64-bar movements — canon/call-response body, then a turnaround, a
    # pause and a drop (rule 10) — and then a brief tintinnabuli passage, so
    # tintinnabuli recurs ~every 180 bars (rule 11).
    blocks: list[str] = []
    for span, base in ((56, 0), (56, 100), (44, 200)):
        blocks.append(f"  [{span}, {_canon_body(signal, intensity, verb, tension, span, base)}]")
        blocks.append(f"  [4, {turn}]")
        blocks.append(f"  [1, {pause}]")
        blocks.append(f"  [3, {drop}]")
    blocks.append(f"  [6, {_tint_passage(signal, intensity, verb)}]")

    header = (
        f"// maelcom · dark {_KEY} {_MODE} · canon & call-response, tintinnabuli ~every 180 bars · "
        f"band={band} · {signal.volume} change{'s' if signal.volume != 1 else ''}"
    )
    return f"{header}\narrange(\n" + ",\n".join(blocks) + "\n).slow(2)"  # largo (rule 5)
