"""**Entrainment 0.1** — brainwave-entrainment ambient, generative journeys.

**Basic mode:** an evolving **multivoice major-chord drone** over a stable low‑A
pedal, punctuated by **chimes** and washes of **colored noise** — a tide rolling
in, or a rainy background. No melody voice, no walking bass. The entrainment rides
a **binaural beat** off the harmony's root where that beat is viable (else the
drone's filter pulse carries it). See ENTRAINMENT.md for the research.

- **Bass (every phase):** a stable low‑A pedal — a1 sub + a2 as every chord's
  lowest voice — so the bottom never walks.
- **Drone (every phase):** an A pedal whose upper *color* evolves (A / add9 /
  maj7 / 6 / sus4 / 5) with long crossfades. Over a slow ~4-min arc it makes a
  deliberate **resolution to a deep D** (an octave down, with G/F color), dwells
  there a while, then returns to A — a resolution, never a walking bass. Rich
  low-passed-saw harmonics, slow filter breath, spatial pan.
- **Entrainment (every phase):** a **binaural** beat from the harmony root A (two
  pure sines a `hz` beat apart, hard L/R, exact via ``freq()``) where viable
  (beat ≤ 30 Hz); for gamma the drone's **filter** pulses at the band rate.
- **Chimes:** occasional, in the warm low‑A‑major register (down an octave, soft
  attack — never a bright ping). Mostly a **prepared‑piano** voice (a muted,
  low‑passed pluck drenched in reverb and ducked so it drifts under the drone),
  sometimes a warm **sine** bell, and now and then a soft **rain stick**.
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

# Per-phase material pools. The harmony sits on an A pedal and, every fourth
# phase, makes a slow *deliberate resolution to D* — a deep octave-down D with G/F
# color — then returns to A. This is a resolution, not a walking bass: the root
# moves at most once per ~2 minutes and only between A and D.
_A_CHORDS = (
    "<[a2,c#3,e3]>",  # A major
    "<[a2,c#3,e3,b3]>",  # A add9
    "<[a2,c#3,e3,g#3]>",  # A major 7
    "<[a2,c#3,e3,f#3]>",  # A 6
    "<[a2,d3,e3]>",  # A sus4
    "<[a2,e3,a3]>",  # A5 (open — a resting point)
)
_D_CHORDS = (  # the resolution — a low D (sub adds d1 an octave down), with G and F
    "<[d2,a2,d3]>",  # D open (fifth)
    "<[d2,f3,a3]>",  # D with F natural (modal color)
    "<[d2,g3,a3]>",  # D with G (sus)
    "<[d2,a2,f3,g3]>",  # D with both F and G
)


_HARMONY_CYCLE = 8  # phases: ~5 on A, then ~3 resolved on D, then back to A


def _phase_harmony(i: int, seed: int) -> tuple[str, str]:
    """A slow harmonic arc: spend a while on the A pedal, resolve to a deep D and
    dwell there a few phases (~1.5 min), then return to A. The root moves at most
    twice per ~4-minute cycle — a resolution, never a walking bass."""
    if i % _HARMONY_CYCLE >= 5:
        return "d", _variant(_D_CHORDS, seed, i, "dchord")
    return "a", _variant(_A_CHORDS, seed, i, "achord")
# A chime is a 1-, 2-, or 3-tone gesture in a4:major that steps down to a
# resolution note — the tonic (A = degree 0) or the upcoming resolution (D = 3).
_CHIME_NS = (1, 2, 3, 5)  # a chime is a 1-, 2-, 3-, or 5-tone gesture
_CHIME_POS = {1: (6,), 2: (3, 6), 3: (2, 4, 6), 5: (1, 2, 3, 4, 6)}  # slot placements per count
# The chime timbre, weighted: mostly prepared piano, then synth bell, some rain stick.
_CHIME_TIMBRES = ("piano", "piano", "piano", "synth", "synth", "rainstick")


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


def _sub_pedal(root: str) -> str:
    """The deep bass pedal on the current root (a1, or d1 when resolved to D);
    long fades so it stays seamless and moves only on the slow resolution."""
    return f'    note("{root}1").s("sine").lpf(120).attack(6).release(10).gain(0.18)'


def _chord_drone(chord: str, hz: float, viable: bool) -> str:
    """The multivoice chord drone — its color evolves each phase; long attack/
    release so chords crossfade. When binaural isn't viable (gamma) its filter
    pulses at the band rate instead."""
    if viable:
        filt = "lpf(sine.range(220,560).slow(24))"
    else:
        filt = f"lpf(sine.range(160,600).fast({max(1, round(hz * _CYCLE_S))}))"
    return (  # long attack/release: chords overlap and crossfade across phases
        f'    note("{chord}").s("sawtooth").attack(8).release(12).{filt}'
        f".pan(sine.range(0.4,0.6).slow(46)).room(0.85).roomsize(9).gain(0.18)"
    )


def _binaural(hz: float) -> list[str]:
    """A binaural beat riding the harmony root A — two pure sines exactly `hz`
    apart, hard L/R — where the beat is viable."""
    if hz > _BINAURAL_MAX_HZ:
        return []
    return [
        f'    freq({_BINAURAL_CARRIER:g}).s("sine").pan(0).attack(8).release(10).room(0.6).roomsize(7).gain(0.11)',
        f'    freq({round(_BINAURAL_CARRIER + hz, 3):g}).s("sine").pan(1).attack(8).release(10).room(0.6).roomsize(7).gain(0.11)',
    ]


def _chime_resolution(i: int, seed: int) -> int:
    """Where the chime resolves: the tonic (A = 0), or the root of the harmonic
    target coming up next (A = 0, or the resolved D = degree 3), chosen at random."""
    if _pick(seed, i, "chimeres", 2) == 0:
        return 0
    return 3 if _phase_harmony(i + 1, seed)[0] == "d" else 0


def _m_chime(i: int, seed: int) -> str:
    """A 1-, 2-, 3-, or 5-tone chime that steps down to the resolution note, in the
    warm low ``a3:major`` register with a soft attack — never a bright alarm ping.
    Mostly a prepared-piano voice (a muted low-passed square pluck, a lot of reverb,
    ducked by a slow quiet gain swell); sometimes a warm sine bell; occasionally a
    soft rain stick. The intended effect is an uninterrupted flow, not a chime that
    pokes out of the wash."""
    n = _CHIME_NS[_pick(seed, i, "chimen", len(_CHIME_NS))]
    res = _chime_resolution(i, seed)
    gesture = list(range(res + n - 1, res - 1, -1))  # step down to the resolution note
    slots = ["~"] * 8
    for k, p in enumerate(_CHIME_POS[n]):
        slots[p] = str(gesture[k])
    cell = "<" + " ".join(slots) + ">"
    timbre = _CHIME_TIMBRES[_pick(seed, i, "chimbre", len(_CHIME_TIMBRES))]
    if timbre == "rainstick":
        # A soft rain stick: sparse high filtered grains with a long reverb wash —
        # gentle, never a fast flutter.
        return (
            '    s("white").struct("~ x ~ ~ x ~ x ~ ~ x ~ x ~ ~ x ~").decay(0.05).sustain(0)'
            ".hpf(2200).lpf(7000).attack(0.001).release(0.3)"
            ".pan(sine.range(0.2,0.8).slow(30)).room(0.92).roomsize(11).gain(0.045)"
        )
    if timbre == "piano":
        # Prepared piano: a MUTED (heavily low-passed) square pluck, a LOT of reverb,
        # and DUCKING via a slow, quiet gain swell — it drifts under the drone.
        return (
            f'    n("{cell}").scale("a3:major").s("square").lpf(760)'
            ".attack(0.01).decay(0.6).sustain(0.03).release(5)"
            ".delay(0.85).delaytime(0.66).delayfeedback(0.62)"
            ".pan(sine.range(0.3,0.7).slow(34)).room(0.95).roomsize(12)"
            ".gain(sine.range(0.03,0.08).slow(26))"
        )
    # Synth: a warm sine bell — low register + a SOFT attack so it blooms in, never
    # a bright ping; the low-pass sits above it as a gentle ceiling.
    return (
        f'    n("{cell}").scale("a3:major").s("sine").lpf(900)'
        ".attack(0.6).release(6).delay(0.9).delaytime(0.66).delayfeedback(0.72)"
        ".pan(sine.range(0.25,0.75).slow(34)).room(0.9).roomsize(10).gain(0.07)"
    )


def _m_noise(i: int, seed: int) -> str:
    """A wave of colored noise that swells and recedes like a TIDE — a long slow
    attack/release plus a very slow gain swell, never a fast (respirator-like)
    flutter. Colored by filtering (brown/soft/airy), drifting spatially."""
    color = ["", ".lpf(500)", ".lpf(900).hpf(150)", ".hpf(2200).lpf(9000)"][
        _pick(seed, i, "noisecol", 4)
    ]
    return (
        f'    s("white"){color}.attack(8).release(10).room(0.85).roomsize(9)'
        f".pan(sine.range(0.3,0.7).slow(48)).gain(sine.range(0.02,0.11).slow(32))"
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
    "1. Basic mode: an evolving A-major-color drone + occasional chimes + colored-noise (tide/rain) waves. No melody voice.",
    "2. An A pedal that, over a slow ~4-min arc, makes a deliberate RESOLUTION to a deep D (octave down, with G/F color), dwells there a while, then returns to A. The root moves only on the resolution — never a walking bass.",
    "3. Entrainment rides a BINAURAL beat off the harmony root A where viable (beat ≤ 30 Hz); for gamma the drone's filter pulses at the band rate. No pulsing tone/melody.",
    "4. The frame drifts slowly downward toward relaxation over ~13–15 min; 16-bar phases.",
    "5. No voice repeats past 16 bars: each phase re-derives its material by a small, consonant step (a new chord voicing, chime, noise).",
    "6. Chimes are occasional 1-, 2-, 3-, or 5-tone gestures that step down and RESOLVE to the tonic (A) or the upcoming resolution (D); long echo; sine or an occasional tamed square.",
    "7. Any white/colored noise is a slow TIDE — long attack/release + a very slow swell, never a fast respirator-like flutter.",
    "8. Low frequencies, rich (low-passed saw) harmonics, heavy reverb + delay echoes, slow spatial pan; nothing enters/leaves abruptly.",
    "9. Deterministic per signal.",
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
        root, chord = _phase_harmony(i, seed)
        layers = [_sub_pedal(root), _chord_drone(chord, hz, viable), *_binaural(hz)]
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
