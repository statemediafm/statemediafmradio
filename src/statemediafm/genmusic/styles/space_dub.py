"""The ``space-dub`` generator — dub sound, techno DJ-grid arrangement.

Dub's building blocks (drum machine + bass synth + lead synth + delay + filter),
arranged with **techno best practices**: everything is on a 4/4 grid, and the
piece is a **long-form arrangement** — elements enter and exit on 8/16-bar phrase
boundaries (intro → bass in → build → drop → breakdown → build → peak → outro),
with a **riser into each drop** and a stripped **breakdown** for tension/release.
That turns a loop into a ~7-minute journey that then repeats, like a techno track.

Voices, on the verified-primitive IR (:mod:`statemediafm.genmusic.ir`):

* **drum machine** — real ``dirt-samples`` one-drop kit (``bd``/``sd`` on beat 3,
  ``hh`` on the off-beats, swung);
* **bass synth** — a deep round triangle playing a *curated* dub/reggae/DnB riff
  (scale degrees, so it's recognizable and transposes by key);
* **lead** — an off-beat **skank** (chord stabs + delay) and an **evolving stab**
  (a techno chord stab whose rhythm alternates and whose filter/pan drift on long
  LFOs), plus a sustained **pad** for the breakdown;
* **riser** — a 16th-note noise roll that builds tension into the drops.

Calm bands lean dub/reggae, busy bands lean DnB; ``synco`` (volatility-led) picks
busier riffs. The **entrainment frequency sets the tempo** — the pulse is the
band's ``carrier_hz`` halved into a musical range (a subharmonic), and the bass is
centered on that grid rather than setting it. Everything is in
**F# minor — the relative minor of Entrainment 0.1's A-major drone** — so the two
share a pitch collection and a gradual crossfade between generators stays
consonant (no clashing third).

Deterministic and golden-testable. The drum machine needs ``dirt-samples`` (the
browser player loads them on Start; the synth voices carry it until then).
"""

from __future__ import annotations

from ...core.models import ActivitySignal
from ..brainwave import band_traits, clamp01
from ..ir import Mod, Piece, Seg, Voice, emit

# Everything is in F# minor — the RELATIVE MINOR of A major, which is the key of
# the Entrainment 0.1 drone (a stable low-A / A-major pedal). Relative minor/major
# share all seven notes, so Space Dub stays dark and dub while a gradual crossfade
# to/from Entrainment 0.1 never hits a clashing third — the F# bass reads as the
# 6th under Entrainment's A. All chords below are diatonic to A major / F# minor.
_BASS_SCALE = "f#1:minor:pentatonic"  # F# A B C# E — a subset of A major

# Dub chord loops (skank harmony), one chosen per signal; F#-minor diatonic.
_PROGRESSIONS = (
    "<F#m9 Bm9 D6 C#m7>",
    "<F#m9 D6 E A6>",
    "<C#m9 Bm9 F#m9 E6>",
)
# Longer wandering chord cycles for the STAB/PAD (8 chords, coprime with phrasing)
# so the harmony keeps moving over minutes; all F#-minor diatonic.
_CHIME_PROG = (
    "<F#m9 A6 D6 Bm9 C#m7 F#m9 E6 Amaj7>",
    "<F#m9 C#m7 Bm9 D6 E6 F#m9 Amaj7 Bm11>",
    "<C#m9 E6 A6 F#m9 Bm9 D6 C#m7 F#m11>",
)
# Curated bass PHRASES (not one-bar loops) as scale degrees over the F#-minor-
# pentatonic root (0=root, 3=5th, 4=b7, 5=octave, -5=octave below). Each is a
# 4-bar `<...>` phrase that MOVES bar to bar and RELENTS — the last bar drops out
# (a bare `~`) or thins right down, so the bass breathes instead of hammering a
# loop. `synco` picks the sparser (calm) or busier (bursty) phrase.
_RIFFS = {
    "dub": (
        "<[0 ~ ~ ~ ~ ~ 3 ~] [0 ~ ~ ~ ~ ~ ~ ~] [~ ~ ~ ~ 3 ~ 0 ~] ~>",
        "<[0 ~ ~ 0 ~ ~ 3 ~] [0 ~ ~ ~ 3 ~ 0 ~] [0 ~ ~ 0 ~ ~ 3 ~] [~ ~ 3 ~ ~ ~ ~ ~]>",
    ),
    "reggae": (
        "<[~ ~ ~ ~ 0 ~ ~ ~] [~ ~ ~ ~ ~ ~ 3 ~] [~ ~ ~ ~ 0 ~ ~ ~] ~>",
        "<[0 ~ ~ 3 ~ 0 ~ 4] [~ ~ 3 ~ ~ 0 ~ ~] [0 ~ ~ 3 ~ 0 ~ ~] ~>",
    ),
    "dnb": (
        "<[0 ~ ~ 5 ~ 0 ~ ~] [~ ~ ~ ~ 0 ~ 5 ~] [0 ~ ~ 5 ~ 0 ~ ~] ~>",
        "<[0 ~ 0 5 0 ~ 3 ~] [0 5 0 ~ 0 ~ 5 ~] [0 ~ 0 5 0 ~ 3 ~] [~ 5 ~ ~ 0 ~ ~ ~]>",
    ),
}
# Drum machine (one-drop): kick + snare on beat 3, hats + skank on the off-beats.
_ONEDROP = "~ ~ ~ ~ x ~ ~ ~"
_OFFBEATS = "~ x ~ x ~ x ~ x"
# The evolving techno stab: a tresillo (3-3-2) alternating with an off-beat accent
# each bar, so the rhythm keeps shifting.
_STAB = "<[x ~ ~ x ~ ~ x ~] [~ x ~ x ~ ~ x ~]>"


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


def _arrangement(seed: int) -> tuple[tuple[str, int, frozenset[str]], ...]:
    """A techno long-form arrangement: (name, bars, active groups). Elements enter
    and exit on 8/16-bar phrase boundaries — intro, bass in, build (riser), drop,
    breakdown (strip to bass + atmosphere), build, peak, outro. Peak length varies
    a touch with the seed, staying on the 8-bar grid."""
    peak = 16 + 8 * (seed % 2)
    return (
        ("intro", 8, frozenset({"drums"})),
        ("bass-in", 8, frozenset({"drums", "bass"})),
        ("build-1", 8, frozenset({"drums", "bass", "riser"})),
        ("drop-1", 16, frozenset({"drums", "bass", "skank", "stab"})),
        ("breakdown", 16, frozenset({"pad", "stab"})),  # bass drops out — the relent
        ("build-2", 8, frozenset({"drums", "bass", "riser"})),
        ("peak", peak, frozenset({"drums", "bass", "skank", "stab", "pad"})),
        ("outro", 8, frozenset({"drums", "bass"})),
    )


def _segs(group: str, pattern: str, arr) -> tuple[Seg, ...]:
    """Build a voice's arrange across the sections: play ``pattern`` where the
    group is active, rest otherwise — the DJ-grid entrance/exit of elements."""
    return tuple(Seg(bars, pattern if group in active else None) for _, bars, active in arr)


def build(signal: ActivitySignal, intensity: float, band: str) -> Piece:
    """Assemble the Space Dub :class:`Piece` — pure data for the IR emitter."""
    intensity = clamp01(intensity)
    tr = band_traits(band)
    density = int(tr["density"])
    lpf = round(tr["lpf"])
    carrier = float(tr["carrier_hz"])
    # The ENTRAINMENT FREQUENCY sets the tempo. The pulse is that carrier halved
    # into a musical range, so carrier/fast is an exact power of two and every beat
    # coincides with an integer number of entrainment pulses. The bass does NOT set
    # the tempo — it is centered on this frequency-derived grid.
    pulse = carrier
    while pulse > 1.6:
        pulse /= 2.0
    fast = round(pulse, 3)

    seed = _seed(signal)
    variant = seed % len(_PROGRESSIONS)
    prog = _PROGRESSIONS[variant]
    pad_prog = _CHIME_PROG[variant]
    scale = _BASS_SCALE  # F# minor — relative minor of Entrainment 0.1's A major
    cut = round(320 + density * 35)  # bass filter base
    # A dark brightness CEILING well below the band's own lpf: Space Dub is meant to
    # pass the time, not command attention, so the stabs/skank/pad stay muted and
    # never open up to the bright top end.
    ceil = min(lpf, 620 + density * 30)  # ~650 (delta) → ~800 (gamma), capped dark

    synco = round(clamp01(0.2 + signal.volatility * 0.6 + (density - 1) * 0.04), 2)
    genre = _genre(density, seed)
    riffs = _RIFFS[genre]
    riff = riffs[min(len(riffs) - 1, round(synco * (len(riffs) - 1)))]

    la = round(0.02 + (6 - density) * 0.004, 3)  # laid-back rubato
    sw = round(0.16 - (density - 1) * 0.02, 2)  # off-beat shuffle
    arr = _arrangement(seed)

    # BASS SYNTH: deep round triangle riff, dark filter breath. Present from
    # "bass-in" through the whole track (including the breakdown — dub keeps the
    # bass under the atmosphere).
    bass = Voice(
        name="bass", kind="note", sound="triangle", scale=scale, gain=0.7,
        decay=0.3, sustain=0.6, late=la, slow=2.0, mods=(Mod("lpf", 150, cut, 10),),
        segments=_segs("bass", riff, arr),
    )
    # LEAD — off-beat skank: short chord stabs, kept dark and quiet so they sit in
    # the wash rather than cutting through (muted lpf, low gain, gentle throw).
    skank = Voice(
        name="skank", kind="chord", sound="sawtooth", chord=prog, gain=0.12,
        decay=0.12, sustain=0.0, lpf=ceil, late=la, slow=2.0,
        fx=(("delay", 0.4), ("delaytime", 0.5), ("delayfeedback", 0.38),
            ("room", 0.6), ("roomsize", 6)),
        segments=_segs("skank", _OFFBEATS, arr),
    )
    # EVOLVING STAB — a chord stab whose rhythm alternates each bar and whose filter
    # + pan drift on long LFOs, over a wandering chord cycle. Capped dark (never
    # opens to the bright top) and quiet — it drifts, it doesn't announce itself.
    stab = Voice(
        name="stab", kind="chord", sound="sawtooth", chord=pad_prog, gain=0.1,
        decay=0.16, sustain=0.0, late=la, slow=2.0,
        fx=(("delay", 0.4), ("delaytime", 0.375), ("delayfeedback", 0.4),
            ("room", 0.5), ("roomsize", 5)),
        mods=(Mod("lpf", 400, ceil, 17), Mod("pan", 0.35, 0.65, 23)),
        segments=_segs("stab", _STAB, arr),
    )
    # PAD — sustained atmosphere for the breakdown; filter/gain drift on long LFOs
    # (31/23/29) so it evolves for hours. Kept under the dark ceiling too.
    pad = Voice(
        name="pad", kind="chord", sound="sawtooth", chord=pad_prog, attack=1.5,
        sustain=1.0, slow=2.0,
        fx=(("room", 0.9), ("roomsize", 8), ("delay", 0.5), ("delaytime", 0.375),
            ("delayfeedback", 0.4)),
        mods=(Mod("lpf", 260, ceil, 31), Mod("pan", 0.3, 0.7, 23), Mod("gain", 0.05, 0.1, 29)),
        segments=_segs("pad", "x ~ ~ ~", arr),
    )
    # RISER — a soft noise swell into the drops; low-passed and quiet so it lifts
    # gently rather than hissing for attention.
    riser = Voice(
        name="riser", kind="perc", sound="white", gain=0.05, decay=0.04, sustain=0.0,
        hpf=1500, lpf=5000, slow=2.0, fx=(("room", 0.5), ("roomsize", 5)),
        segments=_segs("riser", "x*16", arr),
    )
    # DRUM MACHINE — real dirt-samples, one-drop: kick + snare on beat 3, hats on
    # the off-beats. Snare + hats are low-passed and pulled back so the kit keeps
    # time without the snare crack or hat sizzle grabbing the ear.
    kick = Voice(
        name="kick", kind="perc", sound="bd", gain=0.8, lpf=2000, late=la, slow=2.0,
        segments=_segs("drums", _ONEDROP, arr),
    )
    # A distant dub snare "splash": HIGH-PASSED to strip the body (just the airy
    # crack rings out), quiet, drenched in reverb + a long echo so it sits far back,
    # and PANNED on a slow LFO so each hit drifts to a different spot in the field.
    snare = Voice(
        name="snare", kind="perc", sound="sd", gain=0.22, hpf=700, lpf=4000, late=la, slow=2.0,
        fx=(("delay", 0.32), ("delaytime", 0.5), ("delayfeedback", 0.34),
            ("room", 0.88), ("roomsize", 12)),
        mods=(Mod("pan", 0.12, 0.88, 5),),  # spatial: the snare moves across the field
        segments=_segs("drums", _ONEDROP, arr),
    )
    hats = Voice(
        name="hats", kind="perc", sound="hh", gain=0.15, lpf=5500, swing=sw, late=la,
        slow=2.0, segments=_segs("drums", _OFFBEATS, arr),
    )

    header = (
        f"// statemediafm space-dub · band={band} · groove={genre} · "
        f"synco={synco} · intensity={round(intensity, 3)} · {signal.volume} "
        f"change{'s' if signal.volume != 1 else ''}, {signal.participant_count} "
        f"voice{'s' if signal.participant_count != 1 else ''}"
    )
    return Piece(
        header=header, fast=fast,
        voices=(bass, skank, stab, pad, riser, kick, snare, hats),
    )


def render(signal: ActivitySignal, intensity: float, band: str, fade_ms: int = 2000) -> str:
    """Render a space-dub Strudel program: build the IR, then emit verified Strudel."""
    return emit(build(signal, intensity, band))
