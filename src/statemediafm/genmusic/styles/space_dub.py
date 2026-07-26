"""The ``space-dub`` generator — recognizable dub from the classic building blocks.

Dub is a drum machine + a bass synth + a lead synth + delay + a filter, and all
of that is in Strudel's reach. This generator wires exactly those, on the
verified-primitive IR (:mod:`statemediafm.genmusic.ir`):

* **drum machine** — real ``dirt-samples`` (``bd``/``sd``/``hh``) in a one-drop
  kit (kick + snare on beat 3, hats on the off-beats);
* **bass synth** — a deep, round triangle playing a *curated* dub/reggae/DnB riff
  (written as scale degrees, so it's recognizable and transposes by key);
* **lead synth** — an off-beat **skank** (short chord stabs on the up-beats) with
  a dub delay throw, plus a slow evolving pad for atmosphere;
* **delay + filter** — spring-delay on the skank/snare, a dark filter breath on
  the bass.

A phrase has a **shape** (loop → turnaround → pause → drop): everything cuts for
the drop so a clean sub boom lands in the space. Calm bands lean dub/reggae, busy
bands lean DnB; ``synco`` (volatility-led) picks busier riffs; tempo tracks the
band's entrainment ``carrier_hz``; the pad evolves for hours via long,
mutually-prime LFOs.

Deterministic and golden-testable. The drum machine needs ``dirt-samples`` loaded
(the browser player loads them on Start); until then the synth voices carry it.
"""

from __future__ import annotations

from ...core.models import ActivitySignal
from ..brainwave import band_traits, clamp01
from ..ir import Mod, Piece, Seg, Voice, emit

# Minor dub chord loops (skank/pad harmony), one chosen per signal.
_PROGRESSIONS = (
    "<Cm9 Abmaj7 Fm9 Gm7>",
    "<Am9 Fmaj7 Dm9 Em7>",
    "<Ebm9 Bmaj7 Abm9 Bbm7>",
)
# Longer wandering chord cycles for the PAD (8 chords, coprime with the phrase) so
# the atmosphere keeps moving over minutes rather than repeating each bar.
_CHIME_PROG = (
    "<Cm9 Ebmaj7 Abmaj7 Fm9 Gm7 Cm9 Bbmaj7 Fm11>",
    "<Am9 Cmaj7 Fmaj7 Dm9 Em7 Am9 Gmaj7 Dm11>",
    "<Ebm9 Gbmaj7 Bmaj7 Abm9 Bbm7 Ebm9 Dbmaj7 Abm11>",
)
# Curated bass RIFFS as scale degrees over a minor-pentatonic root (0=root, 3=5th,
# 4=b7, 5=octave, -5=octave below). Pitch AND rhythm together — sparse, root-heavy,
# recognizable dub/reggae/DnB lines. Each genre lists sparse → busy for `synco`.
_RIFFS = {
    "dub": (
        "0 ~ ~ ~ ~ ~ 3 ~",
        "0 ~ ~ 0 ~ ~ 3 ~",
        "0 ~ ~ 3 ~ 0 ~ 4",
    ),
    "reggae": (
        "~ ~ ~ ~ 0 ~ 3 ~",
        "0 ~ ~ 3 ~ 0 ~ -2",
        "0 ~ 1 ~ 2 ~ 3 ~",
    ),
    "dnb": (
        "0 ~ ~ 5 ~ 0 ~ 5",
        "0 ~ 0 5 0 ~ 3 ~",
        "0 5 0 3 0 5 0 3",
    ),
}
_TURN = "0 1 2 3 4 3 2 1"  # a walking fill (scale degrees) up into the drop
_DROP = "0 ~ -5 ~ ~ ~ ~ ~"  # root then an octave-below sub boom
_ROOTS = ("c1", "a0", "c1")  # minor-pentatonic root per progression
# The drum machine (one-drop): kick + snare on beat 3, hats on the off-beats.
_ONEDROP = "~ ~ ~ ~ x ~ ~ ~"
_OFFBEATS = "~ x ~ x ~ x ~ x"


def _seed(signal: ActivitySignal) -> int:
    theme_bits = sum(ord(c) for c in "".join(signal.themes)[:12])
    return signal.volume * 7 + signal.participant_count * 13 + int(signal.volatility * 100) + theme_bits


def _genre(density: int, seed: int) -> str:
    """Calm bands lean dub/reggae, busy bands lean DnB; the seed varies it."""
    if density <= 2:
        return "reggae" if seed % 3 == 0 else "dub"
    if density >= 4:
        return "dnb"
    return ("dub", "reggae", "dnb")[seed % 3]


def build(signal: ActivitySignal, intensity: float, band: str) -> Piece:
    """Assemble the Space Dub :class:`Piece` — pure data for the IR emitter."""
    intensity = clamp01(intensity)
    tr = band_traits(band)
    density = int(tr["density"])
    lpf = round(tr["lpf"])
    carrier = float(tr["carrier_hz"])
    # Tempo aligned to the entrainment frequency (sqrt-compressed to stay musical).
    fast = round(0.5 + (carrier**0.5) * 0.143, 2)

    seed = _seed(signal)
    variant = seed % len(_PROGRESSIONS)
    prog = _PROGRESSIONS[variant]
    pad_prog = _CHIME_PROG[variant]
    scale = f"{_ROOTS[variant]}:minor:pentatonic"
    loops = max(3, 7 - density)  # calm bands loop longer before turning around
    cut = round(320 + density * 35)  # bass filter base — a little brighter when busy

    # Syncopation: volatility-led. Picks a busier riff from the genre's sparse→busy
    # list, so bursty activity plays a more syncopated line.
    synco = round(clamp01(0.2 + signal.volatility * 0.6 + (density - 1) * 0.04), 2)
    genre = _genre(density, seed)
    riffs = _RIFFS[genre]
    riff = riffs[min(len(riffs) - 1, round(synco * (len(riffs) - 1)))]

    la = round(0.02 + (6 - density) * 0.004, 3)  # laid-back rubato
    sw = round(0.16 - (density - 1) * 0.02, 2)  # off-beat shuffle for the hats

    # BASS SYNTH: deep round triangle, a curated riff, long sustain so notes ring,
    # a dark filter breath. Prominent — dub bass is up front. Loops → turnaround →
    # then silent under the pause + drop.
    bass = Voice(
        name="bass", kind="note", sound="triangle", scale=scale, gain=0.7,
        decay=0.3, sustain=0.6, slow=2.0, late=la, mods=(Mod("lpf", 150, cut, 10),),
        segments=(Seg(loops, riff), Seg(1, _TURN), Seg(2, None)),
    )
    # The clean sub drop, everything else silent around it.
    drop = Voice(
        name="drop", kind="note", sound="sine", scale=scale, gain=0.6,
        attack=0.005, decay=1.5, sustain=0.0, lpf=100, slow=2.0,
        segments=(Seg(loops + 2, None), Seg(1, _DROP)),
    )
    # LEAD SYNTH — the off-beat skank: short chord stabs on the up-beats, bright,
    # with a dub delay throw. The signature reggae/dub sound.
    skank = Voice(
        name="skank", kind="chord", sound="sawtooth", chord=prog, gain=0.18,
        decay=0.12, sustain=0.0, lpf=1600, slow=2.0, late=la,
        fx=(("delay", 0.45), ("delaytime", 0.5), ("delayfeedback", 0.5),
            ("room", 0.5), ("roomsize", 4)),
        segments=(Seg(loops + 1, _OFFBEATS), Seg(2, None)),
    )
    # Slow evolving pad for atmosphere — wandering chords + long mutually-prime
    # LFOs (31/23/29) from the global clock, so it evolves for hours.
    pad = Voice(
        name="pad", kind="chord", sound="sawtooth", chord=pad_prog, attack=1.5,
        sustain=1.0, slow=2.0,
        fx=(("room", 0.9), ("roomsize", 8), ("delay", 0.5), ("delaytime", 0.375),
            ("delayfeedback", 0.4)),
        mods=(Mod("lpf", 300, lpf, 31), Mod("pan", 0.3, 0.7, 23), Mod("gain", 0.05, 0.1, 29)),
        segments=(Seg(loops + 2, "x ~ ~ ~"), Seg(1, None)),
    )
    # DRUM MACHINE — real dirt-samples in a one-drop kit. Kick + snare land on
    # beat 3; hats ride the off-beats (swung). All cut for the pause + drop.
    kick = Voice(
        name="kick", kind="perc", sound="bd", gain=0.85, slow=2.0, late=la,
        segments=(Seg(loops + 1, _ONEDROP), Seg(2, None)),
    )
    snare = Voice(
        name="snare", kind="perc", sound="sd", gain=0.5, slow=2.0, late=la,
        fx=(("delay", 0.3), ("delaytime", 0.375), ("delayfeedback", 0.35), ("room", 0.4)),
        segments=(Seg(loops + 1, _ONEDROP), Seg(2, None)),
    )
    hats = Voice(
        name="hats", kind="perc", sound="hh", gain=0.3, slow=2.0, swing=sw, late=la,
        segments=(Seg(loops + 1, _OFFBEATS), Seg(2, None)),
    )

    header = (
        f"// statemediafm space-dub · band={band} · groove={genre} · "
        f"synco={synco} · intensity={round(intensity, 3)} · {signal.volume} "
        f"change{'s' if signal.volume != 1 else ''}, {signal.participant_count} "
        f"voice{'s' if signal.participant_count != 1 else ''}"
    )
    return Piece(
        header=header, fast=fast,
        voices=(bass, drop, skank, pad, kick, snare, hats),
    )


def render(signal: ActivitySignal, intensity: float, band: str, fade_ms: int = 2000) -> str:
    """Render a space-dub Strudel program: build the IR, then emit verified Strudel."""
    return emit(build(signal, intensity, band))
