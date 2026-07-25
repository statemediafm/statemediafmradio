"""**Entrainment 0.1** — brainwave-entrainment ambient, generative journeys.

**Basic mode:** an evolving **multivoice major-chord drone** over a stable low‑A
pedal, punctuated by **chimes** and washes of **colored noise** — a tide rolling
in, or a rainy background. No melody voice, no walking bass. The entrainment rides
a **binaural beat** off the harmony's root where that beat is viable (else the
drone's filter pulse carries it). See ENTRAINMENT.md for the research.

- **Bass (every phase):** a stable low‑A pedal — a1 sub + a2 as every chord's
  lowest voice — so the bottom never walks.
- **Drone (every phase):** the upper voices evolve through consonant **A‑major
  chords** (A / D / E / add9…), one per 16‑bar phase; rich low‑passed‑saw
  harmonics, slow filter breath, spatial pan.
- **Entrainment (every phase):** a **binaural** beat from the harmony root A (two
  pure sines a `hz` beat apart, hard L/R, exact via ``freq()``) where viable
  (beat ≤ 30 Hz); for gamma the drone's **filter** pulses at the band rate.
- **Chimes:** occasional sparse high major‑pentatonic bells with long echo.
- **Noise waves:** occasional — a slow **tide** (low‑passed swell) or a **rain**
  hiss (high‑passed grains), drifting spatially.

The frame drifts slowly downward toward relaxation over a ~13–15 min journey of
16‑bar phases; no voice repeats past 16 bars; long attack/release + big reverb and
delay tails bridge every seam. Deterministic per signal.
"""

from __future__ import annotations

from ...core.models import ActivitySignal
from ..brainwave import clamp01

# EEG band centre frequencies (Hz) — the entrainment targets.
_BAND_HZ = {"delta": 2.0, "theta": 6.0, "alpha": 10.0, "beta": 20.0, "gamma": 40.0}
_CYCLE_S = 2.0  # assumed seconds per Strudel cycle (cps 0.5) — the pulse-rate calibration knob
_PHASE_BARS = 16  # a voice may hold at most one 16-bar phase, then it must evolve
_BINAURAL_CARRIER = 220.0  # A3 — the harmony's root, high enough for a viable low-Hz beat
_BINAURAL_MAX_HZ = 30.0  # above this the two tones separate into distinct pitches — not viable

# Per-phase material pools. Every A-major chord voices its LOWEST note on a2, so
# the bass pedals (never walks); the upper voices carry the evolving major harmony.
_CHORDS = (
    "<[a2,c#3,e3]>",  # A
    "<[a2,c#3,e3,b3]>",  # A add9
    "<[a2,d3,f#3]>",  # D / A
    "<[a2,e3,g#3,b3]>",  # E / A (gentle sus over the pedal)
    "<[a2,e3,a3]>",  # open fifth (a resting point)
    "<[a2,c#3,e3] [a2,d3,f#3]>",  # A → D
    "<[a2,d3,f#3] [a2,e3,g#3]>",  # D → E
)
_CHIME_CELLS = ("<~ ~ ~ {d} ~ ~ ~ ~>", "<~ {d} ~ ~ ~ ~ ~ ~>", "<~ ~ ~ ~ ~ ~ {d} ~>")


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


def _pick(seed: int, i: int, tag: str, n: int) -> int:
    return _hash(seed, i, tag) % n


def _variant(pool: tuple[str, ...], seed: int, i: int, tag: str) -> str:
    return pool[_hash(seed, i, tag) % len(pool)]


def _sub_pedal() -> str:
    """The stable low-A bass pedal — never walks."""
    return '    note("a1").s("sine").lpf(120).attack(2).release(3).gain(0.18)'


def _chord_drone(i: int, seed: int, hz: float, viable: bool) -> str:
    """The multivoice major-chord drone — its upper voices evolve each phase. When
    binaural isn't viable (gamma) its filter pulses at the band rate instead."""
    chord = _variant(_CHORDS, seed, i, "chord")
    if viable:
        filt = "lpf(sine.range(220,560).slow(24))"
    else:
        filt = f"lpf(sine.range(160,600).fast({max(1, round(hz * _CYCLE_S))}))"
    return (
        f'    note("{chord}").s("sawtooth").attack(4).release(6).{filt}'
        f".pan(sine.range(0.4,0.6).slow(46)).room(0.85).roomsize(9).gain(0.18)"
    )


def _binaural(hz: float) -> list[str]:
    """A binaural beat riding the harmony root A — two pure sines exactly `hz`
    apart, hard L/R — where the beat is viable."""
    if hz > _BINAURAL_MAX_HZ:
        return []
    return [
        f'    freq({_BINAURAL_CARRIER:g}).s("sine").pan(0).attack(4).release(4).room(0.6).roomsize(7).gain(0.11)',
        f'    freq({round(_BINAURAL_CARRIER + hz, 3):g}).s("sine").pan(1).attack(4).release(4).room(0.6).roomsize(7).gain(0.11)',
    ]


def _m_chime(i: int, seed: int) -> str:
    """A sparse high major-pentatonic chime with a long echo — evolves each phase.
    Sine only, with an occasional square (softened by a low-pass); never a
    triangle or sawtooth."""
    cell = _variant(_CHIME_CELLS, seed, i, "chimepos").format(d=_pick(seed, i, "chime", 5))
    square = _pick(seed, i, "chimewave", 4) == 0  # ~1 in 4 chimes is a (tamed) square
    wave = ".s(\"square\").lpf(2200)" if square else '.s("sine")'
    return (
        f'    n("{cell}").scale("a4:major:pentatonic"){wave}'
        f".attack(0.005).release(3).delay(0.7).delaytime(0.75).delayfeedback(0.55)"
        f".pan(sine.range(0.25,0.75).slow(34)).room(0.9).roomsize(9).gain(0.09)"
    )


def _m_noise(i: int, seed: int) -> str:
    """A wave of colored noise — a slow low-passed TIDE rolling in and out, or a
    high-passed RAIN hiss — drifting spatially."""
    if _pick(seed, i, "noisekind", 2) == 0:  # tide: brown-ish wash, very slow swell
        return (
            '    s("white").lpf(600).room(0.85).roomsize(9)'
            ".pan(sine.range(0.3,0.7).slow(40)).gain(sine.range(0,0.13).slow(28))"
        )
    return (  # rain: airy high-passed grains, steadier
        '    s("white").hpf(2500).lpf(9000).room(0.6).roomsize(7)'
        ".pan(rand).gain(rand.range(0.01,0.05).fast(24))"
    )


def _chime_on(seed: int, i: int) -> bool:
    return _pick(seed, i, "chimeon", 5) < 2  # ~2 of every 5 phases


def _noise_on(seed: int, i: int) -> bool:
    return _pick(seed, i, "noiseon", 5) < 2  # ~2 of every 5 phases


def _frame(seed: int, start_hz: float, n: int) -> list[float]:
    """The entrainment frequency per phase — a slow, seeded drift downward toward
    relaxation (small steps, floored at delta ~2 Hz)."""
    hz = start_hz
    out: list[float] = []
    for i in range(n):
        out.append(hz)
        hz = max(2.0, hz - [0, 1, 1, 2][_pick(seed, i, "frame", 4)])
    return out


RULES: tuple[str, ...] = (
    "1. Basic mode: an evolving multivoice MAJOR-chord drone + occasional chimes + colored-noise (tide/rain) waves. No melody voice.",
    "2. Stable low-A bass pedal (a1 sub + a2 chord root) — the bass NEVER walks; only the upper chord voices evolve.",
    "3. Entrainment rides a BINAURAL beat off the harmony root A where viable (beat ≤ 30 Hz); for gamma the drone's filter pulses at the band rate. No pulsing tone/melody.",
    "4. The frame drifts slowly downward toward relaxation over ~13–15 min; 16-bar phases.",
    "5. No voice repeats past 16 bars: each phase re-derives its material by a small, consonant step (a new chord voicing, chime, noise).",
    "6. Low frequencies, rich (low-passed saw) harmonics, heavy reverb + delay echoes, colored-noise waves, slow spatial pan; nothing enters/leaves abruptly.",
    "7. Deterministic per signal.",
)


def render(signal: ActivitySignal, intensity: float, band: str, fade_ms: int = 2000) -> str:
    intensity = clamp01(intensity)
    seed = _seed(signal)
    n = 24 + (seed % 6)  # 24–29 phases of 16 bars → ~13–15 min
    frame = _frame(seed, _BAND_HZ.get(band, 6.0), n)

    blocks: list[str] = []
    for i in range(n):
        hz = frame[i]
        viable = hz <= _BINAURAL_MAX_HZ
        layers = [_sub_pedal(), _chord_drone(i, seed, hz, viable), *_binaural(hz)]
        if _chime_on(seed, i):
            layers.append(_m_chime(i, seed))
        if _noise_on(seed, i):
            layers.append(_m_noise(i, seed))
        body = ",\n".join(layers)
        blocks.append(f"  [{_PHASE_BARS}, stack(\n{body}\n  )]")

    header = (
        f"// Entrainment 0.1 · {band} journey (basic) · frame {frame[0]:g}→{frame[-1]:g} Hz · "
        f"{n} phases · intensity={round(intensity, 3)}"
    )
    return f"{header}\narrange(\n" + ",\n".join(blocks) + "\n)"
