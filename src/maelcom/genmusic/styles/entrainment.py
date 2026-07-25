"""**Entrainment 0.1** — brainwave-entrainment ambient, as generative journeys.

An associative *grammar* of new-age motifs arranged over a slowly-drifting frame
of entrainment frequencies, producing deterministic ~12–15 minute sonic journeys.
See ENTRAINMENT.md for the research and full design. In brief:

- **Frame (always on):** a low drone + a soft pad + an *entrainment carrier* whose
  rate is the current band's frequency (delta 2 … gamma 40 Hz). Crucially the
  carrier is delivered, per phase, as **amplitude** (isochronic tone), a **filter
  pulse**, or a **pan pulse** — sometimes an effect, not a tone/clock/drum, holds
  the entrainment tempo. The frame drifts slowly downward toward relaxation.
- **Occasional motifs:** binaural sessions (minutes), long sub-bass waves, colored
  **noise waves**, chimes, water drops, rain sticks, a plucked basso-continuo arp,
  and a rare soft drum — scheduled by an associative grammar (affinities +
  cooldowns + a hard cap) so at most a few sound at once and the texture changes
  by ~one element per phase. Nothing enters or leaves abruptly: long attack/release
  and big reverb/delay tails bridge every seam, so the space "slowly reveals
  itself" without jolting the listener's attention or relaxed state.

Aesthetic throughout: low frequencies, rich (low-passed sawtooth) harmonics, heavy
reverb + delay for hypnotic echoes, colored-noise washes, anti-phase **ducking**
between pad and noise, and slow **spatial** auto-pan. Deterministic per signal.
"""

from __future__ import annotations

from ...core.models import ActivitySignal
from ..brainwave import clamp01

# EEG band centre frequencies (Hz) — the entrainment targets.
_BAND_HZ = {"delta": 2.0, "theta": 6.0, "alpha": 10.0, "beta": 20.0, "gamma": 40.0}
_CARRIER = 110.0  # low A carrier for tones/binaural
_CYCLE_S = 2.0  # assumed seconds per Strudel cycle (cps 0.5); the pulse-rate calibration knob
_PHASE_BARS = 16  # a voice may hold at most one 16-bar phase, then it must evolve
_SCALE = "a"  # A minor pentatonic tonal centre (consonant, new-age)

# Per-phase material pools — every voice picks a fresh variant each phase, so no
# ostinato outstays 16 bars (the evolution is small/consonant, so it never jars).
_DRONE_V = ("[a1,e2]", "[a1,a2]", "[a1,e2,a2]", "[e1,e2]", "[a1,d2]")
_PAD_SETS = ("<[0,2,4] [0,3,4]>", "<[0,3,5] [0,2,4]>", "<[0,2,5] [0,4,6]>", "<[0,1,4] [0,3,4]>")
_ARP_CELLS = ("0 ~ 2 ~ 4 ~ 2 ~", "0 ~ 3 ~ 2 ~ 4 ~", "4 ~ 2 ~ 0 ~ 2 ~", "0 2 ~ 4 ~ 3 ~ 2")
_WATER_CELLS = ("~ ~ [7 8] ~ ~ ~ ~ ~", "~ [6 7] ~ ~ ~ ~ [8 9] ~", "~ ~ ~ ~ [7 8] ~ ~ ~")
_DRUM_CELLS = ("<a1 ~ ~ ~ ~ ~ a1 ~>", "<a1 ~ ~ ~ a1 ~ ~ ~>", "<a1 ~ ~ ~ ~ ~ ~ ~>", "<~ ~ a1 ~ ~ ~ a1 ~>")
_CHIME_CELLS = ("<~ ~ ~ {d} ~ ~ ~ ~>", "<~ {d} ~ ~ ~ ~ ~ ~>", "<~ ~ ~ ~ ~ ~ {d} ~>")


def _variant(pool: tuple[str, ...], seed: int, i: int, tag: str) -> str:
    return pool[_hash(seed, i, tag) % len(pool)]


def _seed(signal: ActivitySignal) -> int:
    theme_bits = sum(ord(c) for c in "".join(signal.themes)[:12])
    return (
        signal.volume * 7
        + signal.participant_count * 13
        + int(signal.volatility * 100)
        + theme_bits
    )


def _hash(*parts: object) -> int:
    x = 2166136261
    for p in parts:
        for c in str(p):
            x = ((x ^ ord(c)) * 16777619) & 0xFFFFFFFF
    return x


def _u(seed: int, i: int, tag: str) -> float:
    """Deterministic pseudo-random float in [0, 1)."""
    return (_hash(seed, i, tag) % 100000) / 100000.0


def _pick(seed: int, i: int, tag: str, n: int) -> int:
    return _hash(seed, i, tag) % n


def _pent(octv: int) -> str:
    return f"{_SCALE}{octv}:minor:pentatonic"


# ── The frame: drone + entrainment carrier + pad (present every phase) ───────

def _frame_layers(hz: float, i: int, seed: int) -> list[str]:
    n = max(1, round(hz * _CYCLE_S))  # entrainment pulses per cycle
    verb = 0.85
    # A low drone (root/fifth pedal) — its voicing shifts each phase, over a slow
    # filter breath and slow spatial pan.
    drone = (
        f'    note("<{_variant(_DRONE_V, seed, i, "drone")}>").s("sine").attack(4).release(6)'
        f".lpf(sine.range(170,430).slow(24)).pan(sine.range(0.35,0.65).slow(44))"
        f".room({verb}).roomsize(9).gain(0.22)"
    )
    # The entrainment carrier — delivered as amplitude / filter / pan (rule: an
    # effect may hold the tempo instead of a tone). Rich low-passed-saw harmonics.
    mode = _pick(seed, i, "entrain", 3)
    if mode == 0:  # isochronic amplitude throb
        carrier = (
            f'    freq({_CARRIER:g}).s("sawtooth").lpf(330)'
            f".gain(sine.range(0,0.1).fast({n})).room(0.7).roomsize(8)"
        )
    elif mode == 1:  # the FILTER holds the tempo — cutoff pulses at the band rate
        carrier = (
            f'    note("<[a1,e2,a2]>").s("sawtooth").lpf(sine.range(150,640).fast({n}))'
            f".attack(3).release(4).room(0.75).roomsize(9).gain(0.11)"
        )
    else:  # PAN holds the tempo — a spatial pulse at the band rate
        carrier = (
            f'    freq({_CARRIER:g}).s("sawtooth").lpf(360)'
            f".pan(sine.range(0.1,0.9).fast({n})).gain(0.085).room(0.7).roomsize(8)"
        )
    # A soft modal pad — its progression rotates each phase; ducks anti-phase to
    # the noise wave (they trade presence).
    pad = (
        f'    n("{_variant(_PAD_SETS, seed, i, "pad")}").scale("{_pent(2)}").s("sawtooth")'
        f".attack(3).release(5).lpf(sine.range(280,600).slow(30))"
        f".pan(sine.range(0.6,0.4).slow(52)).room(0.85).roomsize(9)"
        f".gain(sine.range(0.12,0.04).slow(30))"
    )
    return [drone, carrier, pad]


# ── Occasional motifs (scheduled by the grammar) ────────────────────────────

def _m_binaural(i: int, seed: int, hz: float) -> str:
    """A binaural session — pure sine carriers a `beat` Hz apart, hard L/R."""
    fr = round(_CARRIER + hz, 3)
    return (
        f'    stack(freq({_CARRIER:g}).s("sine").pan(0).attack(4).release(4).gain(0.12), '
        f'freq({fr:g}).s("sine").pan(1).attack(4).release(4).gain(0.12))'
    )


def _m_sub_wave(i: int, seed: int, hz: float) -> str:
    """A long, slow sub-bass wave that swells and recedes over many seconds."""
    f = 34 + _pick(seed, i, "sub", 3) * 6  # 34/40/46 Hz
    return (
        f'    freq({f}).s("sine").attack(6).release(8).lpf(90)'
        f".gain(sine.range(0.05,0.18).slow(28))"
    )


def _m_noise_wave(i: int, seed: int, hz: float) -> str:
    """A wave of colored noise — white / brown (low-passed) / airy (high-passed) —
    swelling slowly, ducking anti-phase to the pad, drifting spatially."""
    color = ["", ".lpf(500)", ".hpf(3200)"][_pick(seed, i, "noisecol", 3)]  # brown / airy
    return (
        f'    s("white"){color}.room(0.8).roomsize(9)'
        f".pan(sine.range(0.7,0.3).slow(46)).gain(sine.range(0.02,0.1).slow(30))"
    )


def _m_rainstick(i: int, seed: int, hz: float) -> str:
    """Dense filtered-noise grains scattered across the field (spatial)."""
    return (
        '    s("white").hpf(1800).lpf(7000).pan(rand)'
        ".gain(rand.range(0,0.04).fast(20)).room(0.72).roomsize(8)"
    )


def _m_chime(i: int, seed: int, hz: float) -> str:
    """A sparse bright chime with a long echo (hypnotic delay) and wide reverb —
    pitch and placement evolve each phase."""
    cell = _variant(_CHIME_CELLS, seed, i, "chimepos").format(d=_pick(seed, i, "chime", 5))
    return (
        f'    n("{cell}").scale("{_pent(4)}").s("sine")'
        f".attack(0.005).release(3).delay(0.7).delaytime(0.75).delayfeedback(0.55)"
        f".pan(sine.range(0.25,0.75).slow(34)).room(0.9).roomsize(9).gain(0.09)"
    )


def _m_water(i: int, seed: int, hz: float) -> str:
    """A water drop — a short high plink with a quick rising pitch, echoed; the
    drop pattern evolves each phase."""
    return (
        f'    n("{_variant(_WATER_CELLS, seed, i, "water")}").scale("{_pent(5)}").s("sine")'
        f".attack(0.002).decay(0.09).sustain(0).release(0.25)"
        f".delay(0.4).delaytime(0.5).delayfeedback(0.4).pan(rand).room(0.85).gain(0.08)"
    )


def _m_arp(i: int, seed: int, hz: float) -> str:
    """A gentle plucked basso-continuo arpeggio — warm, low-passed, lightly
    delayed; the arpeggio cell evolves each phase."""
    return (
        f'    n("{_variant(_ARP_CELLS, seed, i, "arp")}").scale("{_pent(3)}").s("sawtooth")'
        f".attack(0.005).decay(0.3).sustain(0.08).release(0.6).lpf(720)"
        f".delay(0.3).delaytime(0.375).delayfeedback(0.35).room(0.75).gain(0.09)"
    )


def _m_drum(i: int, seed: int, hz: float) -> str:
    """A rare, soft low drum motif — a reverbed heartbeat, never a snare; its
    pattern evolves each phase."""
    return (
        f'    note("{_variant(_DRUM_CELLS, seed, i, "drum")}").s("sine").attack(0.002).decay(0.45)'
        ".sustain(0).release(0.3).lpf(190).room(0.6).roomsize(7).gain(0.14)"
    )


_MOTIF = {
    "binaural": _m_binaural,
    "sub_wave": _m_sub_wave,
    "noise_wave": _m_noise_wave,
    "rainstick": _m_rainstick,
    "chime": _m_chime,
    "water": _m_water,
    "arp": _m_arp,
    "drum": _m_drum,
}

# ── The associative grammar ─────────────────────────────────────────────────
# (prob, min_len, max_len, cooldown) in phases. Rare/long things have low prob,
# longer min length ("sessions lasting minutes") and longer cooldowns.
_PARAMS = {
    "arp": (0.45, 2, 4, 1),
    "noise_wave": (0.42, 2, 4, 1),
    "chime": (0.35, 1, 3, 2),
    "water": (0.3, 1, 3, 1),
    "rainstick": (0.3, 2, 3, 2),
    "sub_wave": (0.28, 2, 3, 3),
    "binaural": (0.22, 2, 3, 4),  # occasional sessions lasting minutes
    "drum": (0.15, 1, 2, 4),  # rare
}
# Which motifs associate (attract each other) — the "associative" bias.
_AFFINITY = {
    "chime": ("rainstick", "water"),
    "water": ("rainstick", "chime"),
    "drum": ("arp",),
    "sub_wave": ("binaural",),
    "noise_wave": ("rainstick",),
}
_CAP = 3  # at most this many occasional motifs at once — keep the space uncluttered
_OCC = tuple(_PARAMS)


def _schedule(seed: int, n: int) -> list[list[str]]:
    """Walk the grammar phase by phase. Motifs persist for a bounded length then
    cool down; at most _CAP sound at once; additions are biased toward motifs that
    associate with what's already sounding. The active set changes by ~one element
    per phase, so nothing shifts abruptly."""
    active: dict[str, int] = {}  # motif -> phases remaining
    cool: dict[str, int] = dict.fromkeys(_OCC, 0)
    out: list[list[str]] = []
    for i in range(n):
        for m in list(active):  # age out
            active[m] -= 1
            if active[m] <= 0:
                del active[m]
                cool[m] = _PARAMS[m][3]
        for m in _OCC:
            if m not in active and cool[m] > 0:
                cool[m] -= 1

        def associated(m: str, act: dict[str, int]) -> bool:
            return any(m in _AFFINITY.get(a, ()) or a in _AFFINITY.get(m, ()) for a in act)

        cands = [m for m in _OCC if m not in active and cool[m] == 0]
        cands.sort(key=lambda m: (0 if associated(m, active) else 1, m))  # affiliated first
        for m in cands:
            if len(active) >= _CAP:
                break
            prob, mn, mx, _cd = _PARAMS[m]
            p = prob + (0.2 if associated(m, active) else 0.0)
            if _u(seed, i, "add-" + m) < p:
                active[m] = mn + _pick(seed, i, "len-" + m, mx - mn + 1)
        out.append(sorted(active))
    return out


def _frame(seed: int, start_hz: float, n: int) -> list[float]:
    """The entrainment frequency per phase — a slow, seeded drift downward toward
    relaxation (small steps, floored at delta ~2 Hz), so the target eases the
    listener down over the journey without any jump."""
    hz = start_hz
    out: list[float] = []
    for i in range(n):
        out.append(hz)
        hz = max(2.0, hz - [0, 1, 1, 2][_pick(seed, i, "frame", 4)])
    return out


RULES: tuple[str, ...] = (
    "1. Entrain: a continuous carrier at the band frequency (delta 2 … gamma 40 Hz), the journey's frame.",
    "2. The carrier is delivered as amplitude, a FILTER pulse, or a PAN pulse — an effect may hold the tempo, not just a tone.",
    "3. The frame drifts slowly DOWNWARD toward relaxation over ~12–15 min; small steps, never a jump.",
    "4. An associative grammar of occasional motifs (binaural sessions, sub waves, colored-noise waves, chimes, water, rain stick, arp, rare drum) with affinities, cooldowns and a cap of 3.",
    "5. Nothing enters/leaves abruptly: long attack/release + big reverb/delay tails bridge every seam; ~one change per phase.",
    "6. Low frequencies, rich (low-passed saw) harmonics, heavy reverb + delay echoes, colored-noise washes, anti-phase ducking (pad vs noise), slow spatial auto-pan.",
    "7. No voice repeats unchanged past 16 bars: every phase is 16 bars and each voice re-derives its material (a small, consonant evolution).",
    "8. Deterministic per signal.",
)


def render(signal: ActivitySignal, intensity: float, band: str, fade_ms: int = 2000) -> str:
    intensity = clamp01(intensity)
    seed = _seed(signal)
    n = 24 + (seed % 6)  # 24–29 phases of 16 bars → ~13–15 min
    frame = _frame(seed, _BAND_HZ.get(band, 6.0), n)
    sched = _schedule(seed, n)

    blocks: list[str] = []
    for i in range(n):
        hz = frame[i]
        layers = _frame_layers(hz, i, seed)
        layers += [_MOTIF[m](i, seed, hz) for m in sched[i]]
        body = ",\n".join(layers)
        blocks.append(f"  [{_PHASE_BARS}, stack(\n{body}\n  )]")

    header = (
        f"// Entrainment 0.1 · {band} journey · frame {frame[0]:g}→{frame[-1]:g} Hz · "
        f"{n} phases · intensity={round(intensity, 3)}"
    )
    return f"{header}\narrange(\n" + ",\n".join(blocks) + "\n)"
