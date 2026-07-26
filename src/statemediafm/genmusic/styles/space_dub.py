"""The ``space-dub`` generator — dub/reggae/DnB bass-music parameterized by activity.

Built on the verified-primitive IR (:mod:`statemediafm.genmusic.ir`): genre
knowledge (rhythm banks, chord vocabularies, groove params) produces a
:class:`~statemediafm.genmusic.ir.Piece`, and the IR emitter renders it using only
primitives confirmed to sound in ``@strudel/web`` 1.0.3. So the generator can't
ship a silently-broken program — a test asserts the output uses nothing outside
the whitelist.

The rhythm comes from a **curated bank of 12 bass patterns** (dub, reggae, DnB);
the generator places pitches on a chosen pattern and adds **syncopation** (a
volatility-led variable) off the steady drum grid. A phrase has a **shape**: the
groove loops, plays a turnaround, pauses, then drops to a clean sub. The chord
**chime** answers in the gaps and **evolves for hours** via long, mutually-prime
LFOs + a wandering chord cycle. The feel is **dub-swung and laid back** (swing is
emitted as a split-and-late shuffle, not a risky one-liner), evolving with the
band. Tempo is **aligned to the entrainment frequency** (tracks ``carrier_hz``).

Deterministic: the same ``(signal, intensity, band, fade_ms)`` renders
byte-identical text, so it is golden-file testable.
"""

from __future__ import annotations

from ...core.models import ActivitySignal
from ..brainwave import band_traits, clamp01
from ..ir import Mod, Piece, Seg, Voice, emit

# Minor-9 dub chord loops; one is chosen deterministically from the signal.
_PROGRESSIONS = (
    "<Cm9 Abmaj7 Fm9 Gm7>",
    "<Am9 Fmaj7 Dm9 Em7>",
    "<Ebmaj9 Cm9 Abmaj7 Bb7>",
)
# Longer, wandering chord cycles for the CHIME (8 chords, coprime with the 9-cycle
# phrase) so the harmony keeps moving over minutes instead of repeating each bar.
_CHIME_PROG = (
    "<Cm9 Ebmaj7 Abmaj7 Fm9 Gm7 Cm9 Bbmaj7 Fm11>",
    "<Am9 Cmaj7 Fmaj7 Dm9 Em7 Am9 Gmaj7 Dm11>",
    "<Ebmaj9 Gm7 Cm9 Abmaj7 Bb7 Ebmaj9 Fm9 Cm11>",
)
# Low, root-heavy note vocabularies (one per progression) the generator draws from
# when placing pitches on a rhythm. Root repeats so basslines stay anchored.
_BASS_SCALE = (
    ("c1", "eb1", "g0", "c1", "f0", "bb0", "c1", "ab0"),
    ("a0", "c1", "e1", "a0", "d1", "g0", "a0", "f0"),
    ("eb1", "g0", "bb0", "eb1", "ab0", "c1", "eb1", "f0"),
)
# The bank of 12 genre bass RHYTHMS on a 16-step grid ('x' onset, '.' rest).
_RHYTHMS = (
    ("dub-onedrop", "x.....x.....x..."),
    ("dub-stepper", "x.......x......."),
    ("dub-skank", "x...x.......x..."),
    ("dub-rockers", "x..x....x..x...."),
    ("reggae-onedrop", "....x.......x..."),
    ("reggae-rockers", "x...x...x...x..."),
    ("reggae-walk", "x..x..x..x..x..."),
    ("reggae-skip", "..x..x....x..x.."),
    ("dnb-roll", "x.x.x.x.x.x.x.x."),
    ("dnb-synco", "x..x..x.x..x..x."),
    ("dnb-reese", "x.......x..x...."),
    ("dnb-doubletap", "x..xx...x..xx..."),
)
_DUB_REGGAE = (0, 1, 2, 3, 4, 5, 6, 7)
_DNB = (8, 9, 10, 11)
# The TURNAROUND walk (hand-authored) and the DROP, per progression.
_BASS_TURN = (
    "c1 eb1 f1 g1 ab1 g1 f1 eb1",
    "a0 c1 d1 e1 f1 e1 d1 c1",
    "eb1 g0 ab0 bb0 c1 bb0 ab0 g0",
)
_DROPS = (
    "c1 ~ c0 ~ ~ ~ ~ ~",
    "a1 ~ a0 ~ ~ ~ ~ ~",
    "eb1 ~ eb0 ~ ~ ~ ~ ~",
)
# One-drop kick and skank hat, on the SAME 16-step grid the bass uses.
_KICK = "x.......x......."
_HAT_SPARSE = "....x.......x..."
_HAT_BUSY = "..x..x..x..x..x."
# Two evolving chime stab patterns (A rides the loop, B the turnaround), by density.
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


def _steps(pattern: str) -> str:
    """Expand a compact 'x./' grid string into space-separated mini-notation."""
    return " ".join("x" if c == "x" else "~" for c in pattern)


def _fill(pattern: str, note: str) -> str:
    """Place a fixed ``note`` on every onset of a grid string (rests stay rests)."""
    return " ".join(note if c == "x" else "~" for c in pattern)


def _place(pattern: str, scale: tuple[str, ...], seed: int) -> str:
    """Place pitches from ``scale`` onto a rhythm's onsets; rests stay rests. The
    k-th onset takes ``scale[(seed+k) % len]``, the first the root — anchored but
    varied, and deterministic."""
    out, k = [], 0
    for c in pattern:
        if c == "x":
            out.append(scale[0] if k == 0 else scale[(seed + k) % len(scale)])
            k += 1
        else:
            out.append("~")
    return " ".join(out)


def _syncopate(pattern: str, seed: int, amount: float) -> str:
    """Push off-beat onsets into a rhythm against the steady drum grid. ``amount``
    (0..1) fills off-beat rests — 8th-note "&"s first, then 16ths — never the
    quarter-note beats. Deterministic (seed-ordered)."""
    steps = list(pattern)
    cands = [i for i, c in enumerate(steps) if c == "." and i % 4 != 0]
    cands.sort(key=lambda i: (0 if i % 4 == 2 else 1, (seed * 31 + i * 17) % 97))
    for i in cands[: round(amount * len(cands))]:
        steps[i] = "x"
    return "".join(steps)


def _pick_rhythm(band_density: int, seed: int) -> int:
    """Calm bands lean dub/reggae, busy bands lean DnB; the seed sometimes reaches
    across for texture."""
    if band_density <= 2:
        pool = _DNB if seed % 4 == 0 else _DUB_REGGAE
    elif band_density >= 4:
        pool = _DUB_REGGAE if seed % 3 == 0 else _DNB
    else:
        pool = _DUB_REGGAE + _DNB
    return pool[seed % len(pool)]


def build(signal: ActivitySignal, intensity: float, band: str) -> Piece:
    """Assemble the Space Dub :class:`Piece` from the signal and derived energy.
    Pure data — the IR emitter turns it into Strudel."""
    intensity = clamp01(intensity)
    tr = band_traits(band)
    density = int(tr["density"])
    lpf = round(tr["lpf"])
    carrier = float(tr["carrier_hz"])
    # Tempo aligned to the entrainment frequency: tracks the band's carrier
    # (sqrt-compressed to stay musical). delta≈0.70 → theta≈0.85 → gamma≈1.40.
    fast = round(0.5 + (carrier**0.5) * 0.143, 2)

    seed = _seed(signal)
    variant = seed % len(_PROGRESSIONS)
    prog = _PROGRESSIONS[variant]
    chime_prog = _CHIME_PROG[variant]
    scale = _BASS_SCALE[variant]
    turn, drop = _BASS_TURN[variant], _DROPS[variant]
    stab_a, stab_b = _STAB_A[density], _STAB_B[density]
    loops = max(3, 7 - density)  # calm bands loop longer before turning around
    cut = round(120 + density * 60)  # bass filter base cutoff — darker when calm

    synco = round(clamp01(0.2 + signal.volatility * 0.6 + (density - 1) * 0.04), 2)
    groove_name, groove = _RHYTHMS[_pick_rhythm(density, seed)]
    loop_seq = _place(_syncopate(groove, seed, synco), scale, seed)
    hat = _HAT_BUSY if density >= 3 else _HAT_SPARSE

    # Dub groove: a laid-back shuffle that evolves — calmer bands swing wider and
    # lay back further. The bass swings uniformly; the turnaround rides it.
    sw = round(0.18 - (density - 1) * 0.02, 2)
    la = round(0.02 + (6 - density) * 0.004, 3)
    kick_gain = round(0.15 + intensity * 0.13, 2)

    # BASS: the chosen genre rhythm (pitched) → turnaround → then silent under the
    # pause + drop. A filter breath gives it movement; short envelope = plucky dub.
    bass = Voice(
        name="bass", kind="note", sound="sawtooth", gain=0.5, decay=0.28, sustain=0.2,
        slow=2.0, swing=sw, late=la, mods=(Mod("lpf", 90, cut, 6),),
        segments=(Seg(loops, loop_seq), Seg(1, turn), Seg(2, None)),
    )
    # The clean sub drop lands dead on the beat while everything else is silent.
    drop_voice = Voice(
        name="drop", kind="note", sound="sine", gain=0.55, attack=0.005, decay=1.4,
        sustain=0.0, lpf=110, slow=2.0,
        segments=(Seg(loops + 2, None), Seg(1, drop)),
    )
    # CHIME: wandering chords, answering the bass; filter/pan/gain on long
    # mutually-prime LFOs (31/23/29) so it evolves for hours and never resets.
    chime = Voice(
        name="chime", kind="chord", sound="sawtooth", chord=chime_prog, slow=2.0, late=la,
        fx=(("delay", 0.5), ("delaytime", 0.375), ("delayfeedback", 0.5),
            ("room", 0.7), ("roomsize", 6)),
        mods=(Mod("lpf", 400, lpf, 31), Mod("pan", 0.32, 0.68, 23), Mod("gain", 0.11, 0.18, 29)),
        segments=(Seg(loops, f"<[{stab_a}] [{stab_b}]>"), Seg(1, stab_b), Seg(2, None)),
    )
    # One ringing chime over the drop (base chord, big reverb).
    chime_hit = Voice(
        name="chime_hit", kind="chord", sound="sawtooth", chord=prog, gain=0.14, lpf=lpf,
        slow=2.0, fx=(("room", 0.9), ("roomsize", 8), ("delay", 0.6),
                      ("delaytime", 0.5), ("delayfeedback", 0.6)),
        segments=(Seg(loops + 2, None), Seg(1, "x ~ ~ ~")),
    )
    # One-drop kick, grid-locked to the bass, laid back; cuts for the pause + drop.
    kick = Voice(
        name="kick", kind="note", sound="sine", gain=kick_gain, decay=0.2, sustain=0.0,
        slow=2.0, late=la,
        segments=(Seg(loops + 1, _fill(_KICK, "c1")), Seg(2, None)),
    )
    # Off-beat skank hat, grid-locked, swung — carries the audible shuffle.
    hat_voice = Voice(
        name="hat", kind="perc", sound="white", gain=0.06, decay=0.05, sustain=0.0,
        hpf=6500, slow=2.0, swing=sw, late=la, fx=(("room", 0.4), ("roomsize", 3)),
        segments=(Seg(loops + 1, _steps(hat)), Seg(2, None)),
    )

    header = (
        f"// statemediafm space-dub · band={band} · groove={groove_name} · "
        f"synco={synco} · intensity={round(intensity, 3)} · {signal.volume} "
        f"change{'s' if signal.volume != 1 else ''}, {signal.participant_count} "
        f"voice{'s' if signal.participant_count != 1 else ''}"
    )
    return Piece(header=header, voices=(bass, drop_voice, chime, chime_hit, kick, hat_voice), fast=fast)


def render(signal: ActivitySignal, intensity: float, band: str, fade_ms: int = 2000) -> str:
    """Render a space-dub Strudel program: build the IR, then emit verified Strudel."""
    return emit(build(signal, intensity, band))
