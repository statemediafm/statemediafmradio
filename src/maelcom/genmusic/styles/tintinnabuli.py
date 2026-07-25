"""The default generative style — dark, low, slow-canon ambient with rare glints.

This module is built to an explicit **composition rule base** — see the ``RULES``
tuple below, the single source of truth we build up from. In short: **Dorian**
throughout (no minor keys), everything **low** and **low-passed**; the texture is
slow, theta-paced **canons and call-and-response** whose voices fade in and out;
the piece evolves both **rhythmically and tonally every ~2 minutes** (a new
adjacent key + turnaround/pause/drop); bright **glints** from the parallel major
appear rarely and high; a delayed **chime** rings out every 64 bars; a brief
**tintinnabuli** (Pärt M/T) passage recurs ~every 180 bars; no drums.

Voices use a dark **modified-piano synth** — a heavily filtered sawtooth with a
soft envelope (no piano soundfont is loaded in @strudel/web).
"""

from __future__ import annotations

from ...core.models import ActivitySignal
from ..arrange import pivot_key
from ..brainwave import clamp01

_KEY = "G"  # home key
_MODE = "dorian"  # Dorian mode throughout (no minor keys)
_TRIAD = (0, 2, 4)  # scale degrees of the tonic triad (root, third, fifth)
_TOP = 7  # melodic ceiling in scale degrees (octave 1) — keep everything low

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
    and (when ``sparse``) each sounding bar keeps only beats 1 and 3. One bar per
    cycle: ``<[..] [..] ...>``."""
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
    "1. Dorian mode throughout, no minor keys; home key G.",
    "2. Modulate only to adjacent / consonant circle-of-fifths Dorian keys (G D A E) — one per movement.",
    "3. Bed & voices low (octave 1, ~49-98 Hz); nothing high except the glints (rule 15).",
    "4. Low-pass the bed & voices (no cutoff above ~700 Hz) — nothing harsh; the glints are the exception.",
    "5. 4/4, largo (.slow(2)); it should float, not plod.",
    "6. Texture is canons and call-and-response; voices come and go and trade off.",
    "7. Base canons are SLOW — theta-paced, meditative (.fast(1)); motion/brightness comes from the glints.",
    "8. Fractal branching, only 1-2 branches (a leader + a canon follower + a response).",
    "9. No ostinato longer than 16 bars; material re-varies every <=14 bars.",
    "10. Every ~2 minutes (a ~30-bar movement) evolve BOTH rhythmically (turnaround/pause/drop) AND tonally (a new key).",
    "11. Tintinnabuli (M/T voices) only briefly, ~every 180 bars.",
    "12. No drum kit / snare / noise percussion (a pitched sub-bass pedal pulse is fine — rule 19).",
    "13. A burst of news swells the tonic triad — a consonant emphasis.",
    "14. Deterministic: the same signal always renders the same music.",
    "15. Glints from the parallel major (B, F#), bright & high (G4:major, up to ~880 Hz, filter 1400): rare — one bar every 32, rotating cells.",
    "16. Canon voices fade in and out over ~1-2 minutes (slow, staggered gain LFOs).",
    "17. A delayed chime rings out one beat every 64 bars — many repeats over ~2 bars, a theta-rate LFO on the delay.",
    "18. Sometimes, after ~3 minutes, a 16-bar full silence.",
    "19. A deep sub-bass G pedal — a single quarter-note hit every 24 bars, long release — underpins everything.",
)

_CALL = (True, True, False, False, True, True, False, False)  # leader sings…
_RESP = (False, False, True, True, False, False, True, True)  # …responder answers in the gaps
_CANON_LATE = 3  # bars the canon follower trails the leader (well apart in time)
_CANON_FAST = 1  # theta-slow, meditative base canons (rule 7)
_KEYS = ("G", "D", "A", "E")  # adjacent Dorian keys (sharp-side, so the glints stay consonant)
_GLINT_SCALE = f"{_KEY}4:major"  # bright parallel-major register for the glints (rules 15, 17)


def _sc(key: str, octv: int, mode: str = _MODE) -> str:
    return f"{key}{octv}:{mode}"


def _key_walk(seed: int, n: int) -> list[str]:
    """An adjacent walk through the Dorian key window from G (rule 2)."""
    idx = 0
    out: list[str] = []
    for i in range(n):
        out.append(_KEYS[idx])
        idx = min(len(_KEYS) - 1, max(0, idx + (1 if (seed >> i) & 1 else -1)))
    return out


def _bed(key: str, verb: float) -> list[str]:
    """The sustained low ground (rules 3-5): a held root/fifth drone and a slow
    quartal pad, both deep and low-passed, so the harmony floats without a pulse."""
    sc1 = _sc(key, 1)
    drone = f'    n("<[0,4]>").scale("{sc1}").s("sine").attack(0.4).release(1.6).lpf(150).gain(0.3)'
    pad = (
        f'    n("<[0,3,4] [0,4,7]>").scale("{sc1}").s("sawtooth").detune(0.08)'
        f".lpf(sine.range(220,480).slow(8)).room({verb}).roomsize(8).gain(0.16)"
    )
    return [drone, pad]


def _fade(base: float, period: int) -> str:
    """A slow gain LFO so a canon voice fades in and out over ~1-2 minutes
    (period is in bars; ~4 s/bar → 18 bars ≈ 72 s). Rule 16."""
    return f"sine.range({round(base * 0.12, 3)},{base}).slow({period})"


def _voice(key: str, bars_str: str, verb: float, gain_expr: object, extra: str = "") -> str:
    """A dark-piano melodic voice (rules 3-7): low, low-passed, theta-slow. The
    gain may be a number or a fade LFO expression (rule 16)."""
    return (
        f'    n("{bars_str}").scale("{_sc(key, 1)}").{_PIANO}.detune(0.06).fast({_CANON_FAST})'
        f"{extra}.room({verb}).roomsize(7).gain({gain_expr})"
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


def _canon_chunk(signal: ActivitySignal, key: str, verb: float, tension: float, salt: int) -> str:
    """Canon + call-and-response (rules 6-8): a leader with two branches — a canon
    follower trailing by _CANON_LATE bars and a response answering in the gaps.
    Each voice fades in and out on its own slow LFO (rule 16)."""
    line = _m_voice(signal, 32, salt)
    lead = _bars(line, _CALL, sparse=False)
    resp = _bars(line, _RESP, sparse=False)
    layers = _bed(key, verb)
    layers.append(_voice(key, lead, verb, _fade(0.32, 48)))  # leader (call) — slow fade
    layers.append(_voice(key, lead, verb, _fade(0.2, 64), extra=f".late({_CANON_LATE})"))  # canon — slower
    layers.append(_voice(key, resp, verb, _fade(0.26, 56)))  # response — slow, offset phase
    if tension > 0.05:  # rule 13
        g = round(0.05 + tension * 0.15, 2)
        layers.append(
            f'    n("<[0,2,4] ~ ~ ~>").scale("{_sc(key, 1)}").s("sawtooth")'
            f".lpf(400).attack(0.05).release(1.2).room(0.8).roomsize(8).gain({g})"
        )
    return _stack(layers)


def _canon_body(signal: ActivitySignal, key: str, verb: float, tension: float, span: int, base: int) -> str:
    """A canon/call-response movement body of ``span`` bars, its material varying
    every <=14 bars so no ostinato outstays 16 bars (rule 9)."""
    parts = [
        f"[{sz}, {_canon_chunk(signal, key, verb, tension, base + 1 + k * 13)}]"
        for k, sz in enumerate(_split(span))
    ]
    return "arrange(" + ", ".join(parts) + ")"


def _turnaround(k0: str, k1: str, verb: float) -> str:
    """A descending quartal turnaround that pivots from k0 toward k1 (rules 2, 10)
    via a scale journey through the pivot key."""
    piv = pivot_key(k0, k1)
    journey = f'"<{_sc(k0, 1)} {_sc(piv, 1)} {_sc(piv, 1)} {_sc(k1, 1)}>"'
    fig = (
        f'    n("<[7 4 3 0] [4 2 0 ~]>").scale({journey}).{_PIANO}'
        f".detune(0.06).fast({_CANON_FAST}).room({verb}).roomsize(7).gain(0.3)"
    )
    return _stack([*_bed(k0, verb), fig])


def _pause(key: str, verb: float) -> str:
    """A breath — the bed thins to a lingering root (rule 10)."""
    return _stack(
        [f'    n("0").scale("{_sc(key, 1)}").s("sine").attack(0.6).release(2.6).lpf(120).gain(0.2)']
    )


def _drop(signal: ActivitySignal, key: str, verb: float) -> str:
    """The drop — a deep sub-root and the canon crashing back in (rule 10)."""
    lead = _bars(_m_voice(signal, 16, salt=3), (True, True, True, True), sparse=False)
    return _stack(
        [
            *_bed(key, verb),
            f'    n("<0 ~ ~ ~>").scale("{_sc(key, 1)}").s("sine").lpf(110).attack(0.002).decay(0.6).sustain(0).gain(0.34)',
            _voice(key, lead, verb, 0.32),
            _voice(key, lead, verb, 0.2, extra=f".late({_CANON_LATE - 1})"),
        ]
    )


def _movement(signal: ActivitySignal, k0: str, k1: str, verb: float, tension: float, base: int) -> str:
    """A ~30-bar movement in key ``k0`` that evolves rhythmically and tonally
    (rule 10): a canon body, a turnaround pivoting toward ``k1``, a pause, a drop."""
    body = _canon_body(signal, k0, verb, tension, 22, base)
    return (
        "arrange("
        f"[22, {body}], "
        f"[4, {_turnaround(k0, k1, verb)}], "
        f"[1, {_pause(k0, verb)}], "
        f"[3, {_drop(signal, k0, verb)}])"
    )


def _tint_passage(signal: ActivitySignal, key: str, verb: float) -> str:
    """The rare tintinnabuli passage — M-voice + T-voice shadow (rule 11)."""
    m = _m_voice(signal, 16, salt=7)
    t = _t_voice(m)
    full = (True, True, True, True)
    return _stack(
        [
            *_bed(key, verb),
            _voice(key, _bars(m, full, sparse=False), verb, 0.32),
            _voice(key, _bars(t, full, sparse=False), verb, 0.26),
        ]
    )


def _glint(cell: str) -> str:
    """One bar of bright parallel-major chime (rule 15)."""
    return (
        f'n("{cell}").scale("{_GLINT_SCALE}").s("sawtooth").detune(0.1)'
        f".lpf(1400).attack(0.01).release(2.0).room(0.9).roomsize(8).gain(0.1)"
    )


def _glint_overlay(signal: ActivitySignal) -> str:
    """Rare glints (rule 15): one bar of chime every 32, cells rotating so the
    sequence changes then restarts."""
    cells = ("~ 2 ~ 6 ~ ~ 4 ~", "6 ~ ~ 8 ~ 4 ~ 2", "~ 4 ~ 2 ~ 6 ~ ~", "2 ~ 6 ~ 8 ~ 6 ~")
    s = _seed(signal)
    parts: list[str] = []
    for k in range(4):
        parts.append(f"  [1, {_glint(cells[(s + k) % len(cells)])}]")  # one bar of chime…
        parts.append("  [31, silence]")  # …then silent for 31 more (rare)
    return "arrange(\n" + ",\n".join(parts) + "\n)"


def _chime_delay_overlay(signal: ActivitySignal) -> str:
    """A delayed chime one beat every 64 bars (rule 17): a bright note drenched in
    a many-repeat delay (~2 bars of tail), the delay time wobbled by a theta-rate
    LFO (sine.fast(24) ≈ 6 Hz at ~4 s/bar)."""
    note = (_seed(signal) % 5) * 2  # a bright even degree 0..8
    chime = (
        f'n("{note} ~ ~ ~").scale("{_GLINT_SCALE}").s("sawtooth").detune(0.1).lpf(1400)'
        ".delay(0.9).delaytime(sine.range(0.34,0.46).fast(24)).delayfeedback(0.86)"
        ".room(0.7).roomsize(8).gain(0.13)"
    )
    return f"arrange([1, {chime}], [63, silence])"


def _sub_bass() -> str:
    """A deep sub-bass pedal on G (a tone shared by every key in the window, so it
    stays consonant through modulation) — a single quarter-note hit every 24 bars
    with a long release, so the sub swells and rings out (rule 19)."""
    return (
        '  arrange([1, note("g1 ~ ~ ~").s("sine").attack(0.2).release(8).lpf(100).gain(0.3)], '
        "[23, silence])"
    )


def render(signal: ActivitySignal, intensity: float, band: str, fade_ms: int = 2000) -> str:
    intensity = clamp01(intensity)
    verb = round(0.6 + (1.0 - intensity) * 0.25, 2)  # lusher reverb when calm
    tension = clamp01((signal.volume - 4) / 24.0)  # a burst of news events → swell (rule 13)
    seed = _seed(signal)

    # Six ~30-bar movements (~2 min each), modulating through adjacent Dorian keys
    # and each evolving rhythmically (rule 10); a brief tintinnabuli passage closes
    # the loop, ~every 180 bars (rule 11).
    keys = _key_walk(seed, 6)
    blocks: list[str] = []
    for i, k in enumerate(keys):
        nk = keys[(i + 1) % len(keys)]
        blocks.append(f"  [30, {_movement(signal, k, nk, verb, tension, i * 50)}]")
        if i == 1 and seed % 2 == 0:  # sometimes, ~3-4 min in, a 16-bar full silence (rule 18)
            blocks.append("  [16, silence]")
    blocks.append(f"  [6, {_tint_passage(signal, keys[0], verb)}]")
    main = "arrange(\n" + ",\n".join(blocks) + "\n)"

    header = (
        f"// maelcom tintinnabuli · dark {keys[0]} {_MODE} · slow canons, rare glints, "
        f"modulating ~every 2 min, tintinnabuli ~every 180 bars · band={band} · "
        f"{signal.volume} change{'s' if signal.volume != 1 else ''}"
    )
    # The main arrangement plus independent long-form overlays: a sub-bass pedal
    # pulse, the rare glints, and the delayed chime.
    return (
        f"{header}\nstack(\n{main},\n{_sub_bass()},\n{_glint_overlay(signal)},\n"
        f"  {_chime_delay_overlay(signal)}\n).slow(2)"
    )
