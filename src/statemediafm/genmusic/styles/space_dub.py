"""The ``space-dub`` generator — deep dub/reggae/DnB bass-music parameterized by activity.

Not purely generative: the rhythm comes from a **curated bank of 12 bass patterns**
drawn from dub, reggae and drum-and-bass (their onsets *and* rests), and the
generator places *pitches* onto the chosen pattern. The percussion (one-drop kick,
off-beat skank hat) locks to the same 16-step grid, so bass and drums interlock.
A phrase has a **shape**: the chosen groove loops a few times, plays a turnaround,
pauses, then drops to a clean sub boom. The chord chime answers in the gaps.

The feel is dub-swung and laid back — a gentle 8th-note shuffle (``swingBy``) with
everything a hair behind the beat (a rubato ``late``). Both **evolve**: calmer
bands swing wider and lay back further, busier bands tighten; within a phrase the
loop lolls back while the turnaround straightens into the drop. Tempo is **aligned
to the entrainment frequency** — it tracks the band's ``carrier_hz`` so the groove
speeds up with the brainwave band. Calm bands lean dub/reggae; busy bands lean DnB
sub-rolls; either way the seed sometimes reaches across for texture.

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
# Low, root-heavy note vocabularies (one per progression) the generator draws from
# when placing pitches on a rhythm. Root repeats so basslines stay anchored.
_BASS_SCALE = (
    ("c1", "eb1", "g0", "c1", "f0", "bb0", "c1", "ab0"),
    ("a0", "c1", "e1", "a0", "d1", "g0", "a0", "f0"),
    ("eb1", "g0", "bb0", "eb1", "ab0", "c1", "eb1", "f0"),
)
# The bank of 12 genre bass RHYTHMS on a 16-step grid ('x' onset, '.' rest). The
# generator places pitches on the onsets; the rests are part of the groove.
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
# One-drop kick and skank hat, on the SAME 16-step grid the bass uses, so the
# drums interlock with the groove. Hat busies up for higher-density bands.
_KICK = "x.......x......."
_HAT_SPARSE = "....x.......x..."
_HAT_BUSY = "..x..x..x..x..x."
# Two evolving chime patterns (A rides the loop, B the turnaround), by density.
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


def _place(pattern: str, scale: tuple[str, ...], seed: int) -> str:
    """Place pitches from ``scale`` onto the onsets of a rhythm; rests stay rests.
    Deterministic: the k-th onset takes ``scale[(seed+k) % len]``, the first the
    root, so the line varies but stays anchored."""
    out, k = [], 0
    for c in pattern:
        if c == "x":
            out.append(scale[0] if k == 0 else scale[(seed + k) % len(scale)])
            k += 1
        else:
            out.append("~")
    return " ".join(out)


def _pick_rhythm(band_density: int, seed: int) -> int:
    """Choose a bass rhythm: calm bands lean dub/reggae, busy bands lean DnB, but
    the seed sometimes reaches across for texture."""
    if band_density <= 2:
        pool = _DNB if seed % 4 == 0 else _DUB_REGGAE
    elif band_density >= 4:
        pool = _DUB_REGGAE if seed % 3 == 0 else _DNB
    else:
        pool = _DUB_REGGAE + _DNB
    return pool[seed % len(pool)]


def render(signal: ActivitySignal, intensity: float, band: str, fade_ms: int = 2000) -> str:
    """Render a space-dub Strudel program from the signal and derived energy."""
    intensity = clamp01(intensity)
    tr = band_traits(band)
    density = int(tr["density"])
    lpf = round(tr["lpf"])
    carrier = float(tr["carrier_hz"])
    # Tempo aligned to the entrainment frequency: it tracks the band's carrier
    # (sqrt-compressed to stay musical), so the groove speeds up with the band.
    fast = round(0.5 + (carrier**0.5) * 0.143, 2)  # delta≈0.70 → theta≈0.85 → gamma≈1.40

    seed = _seed(signal)
    variant = seed % len(_PROGRESSIONS)
    prog = _PROGRESSIONS[variant]
    scale = _BASS_SCALE[variant]
    turn, drop = _BASS_TURN[variant], _DROPS[variant]
    stab_a, stab_b = _STAB_A[density], _STAB_B[density]
    loops = max(3, 7 - density)  # calm bands loop longer before turning around
    cut = round(120 + density * 60)  # acid filter base cutoff — darker when calm

    groove_name, groove = _RHYTHMS[_pick_rhythm(density, seed)]
    loop_seq = _place(groove, scale, seed)
    hat = _HAT_BUSY if density >= 3 else _HAT_SPARSE

    # Dub groove: a laid-back 8th-note shuffle that evolves with the band; the
    # turnaround tightens (half swing) and pushes forward (less lay-back).
    sw = round(0.18 - (density - 1) * 0.02, 2)
    sw_turn = round(sw * 0.5, 2)
    la = round(0.02 + (6 - density) * 0.004, 3)
    la_turn = round(la * 0.4, 3)

    def _acid(seq: str, late: float, swing: float) -> str:
        # Sine-leaning sawtooth kept dark by a resonant, percussive filter envelope
        # (TB-303 acid); low gain to keep the sub clean. Swung + laid back.
        return (
            f'note("{seq}").s("sawtooth").lpf(sine.range(90, {cut}).slow(6)).lpq(7)'
            ".lpenv(2.5).lpattack(0.01).lpdecay(0.16).lpsustain(0.25)"
            f'.decay(0.28).sustain(0.2).swingBy({swing}, 2).late({late})'
        )

    def _chime(struct: str) -> str:
        # The chord chime, tape delay + reverb, locked to the bass swing + lay-back.
        return (
            f'chord("{prog}").voicing().s("sawtooth").struct("{struct}")'
            f".lpf(sine.range(400, {lpf}).slow(4)).lpq(4).delay(0.5).delaytime(0.375)"
            f".delayfeedback(0.5).room(0.7).roomsize(6).gain(0.16).swingBy({sw}, 2).late({la})"
        )

    kick_gain = round(0.15 + intensity * 0.13, 2)
    layers = [
        # BASS: the chosen genre rhythm (pitched) loops → turnaround → pause → drop.
        (
            f'  arrange([{loops}, {_acid(loop_seq, la, sw)}], [1, {_acid(turn, la_turn, sw_turn)}], '
            f'[1, silence], [1, note("{drop}").s("sine").attack(0.005).decay(1.4).sustain(0)'
            ".lpf(110).gain(0.55)]).gain(0.5).slow(2)"
        ),
        # CHIME rides the SAME shape + pocket, answering in the bass's rests.
        (
            f'  arrange([{loops}, {_chime(stab_a)}], [1, {_chime(stab_b)}], [1, silence], '
            f'[1, chord("{prog}").voicing().s("sawtooth").struct("x ~ ~ ~").lpf({lpf})'
            ".room(0.9).roomsize(8).delay(0.6).delaytime(0.5).delayfeedback(0.6).gain(0.14)])"
            ".slow(2)"
        ),
        # One-drop kick, grid-locked to the bass, laid back; cuts for pause + drop.
        (
            f'  arrange([{loops + 1}, note("c1").s("sine").struct("{_steps(_KICK)}")'
            f".decay(0.2).sustain(0).gain({kick_gain}).late({la})], [2, silence]).slow(2)"
        ),
        # Off-beat skank hat, grid-locked, swung — carries the audible shuffle.
        (
            f'  arrange([{loops + 1}, s("white").struct("{_steps(hat)}").decay(0.05).sustain(0)'
            f'.hpf(6500).room(0.4).roomsize(3).gain(0.06).swingBy({sw}, 2).late({la})], '
            "[2, silence]).slow(2)"
        ),
    ]

    header = (
        f"// statemediafm space-dub · band={band} · groove={groove_name} · "
        f"intensity={round(intensity, 3)} · {signal.volume} "
        f"change{'s' if signal.volume != 1 else ''}, {signal.participant_count} "
        f"voice{'s' if signal.participant_count != 1 else ''}"
    )
    body = ",\n".join(layers)
    return f"{header}\nstack(\n{body}\n).fast({fast})"
