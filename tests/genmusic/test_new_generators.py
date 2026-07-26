"""Tests for the full band-trait mapping and the Space Dub / Modular Bleep models."""

from __future__ import annotations

import pytest

from statemediafm.core.models import ActivitySignal
from statemediafm.genmusic import compose
from statemediafm.genmusic.brainwave import BAND_TRAITS, band_traits
from statemediafm.genmusic.styles import AMBIENT_MODELS, STYLES, modular_bleep, space_dub

_BANDS = ["delta", "theta", "alpha", "beta", "gamma"]
# @strudel/web 1.0.3 has none of these — emitting one makes the whole program
# fail silently, so no generator may.
_FORBIDDEN = ("setcps", "setcpm", "fadeIn", "unison(")


def _signal(volume: int, participants: int, volatility: float = 0.3) -> ActivitySignal:
    voices = {f"dev{i}": v for i, v in enumerate(["a", "b", "c"][:participants])}
    return ActivitySignal(
        window_s=3600.0, volume=volume, volatility=volatility,
        participant_count=participants, themes=["scheduler"], actor_voices=voices,
    )


def test_band_traits_cover_all_bands_and_are_monotonic():
    assert set(BAND_TRAITS) == set(_BANDS)
    for key in ("carrier_hz", "density", "lpf", "motion"):
        vals = [BAND_TRAITS[b][key] for b in _BANDS]  # calm → focused
        assert vals == sorted(vals) and vals[0] < vals[-1], key
    assert band_traits("nonsense") == BAND_TRAITS["theta"]  # safe default


def test_new_models_registered():
    for name in ("Space Dub", "Modular Bleep"):
        assert name in AMBIENT_MODELS and name in STYLES


@pytest.mark.parametrize("render", [space_dub.render, modular_bleep.render])
@pytest.mark.parametrize("band", _BANDS)
def test_generator_renders_valid_strudel_for_every_band(render, band):
    text = render(_signal(12, 3), 0.5, band)
    assert text.startswith("//") and "stack(" in text and text.rstrip().endswith(")")
    assert band in text
    for bad in _FORBIDDEN:
        assert bad not in text


@pytest.mark.parametrize("style", ["Space Dub", "Modular Bleep"])
def test_generators_are_deterministic(style):
    sig = _signal(20, 3, 0.6)
    a = compose(sig, style=style)
    b = compose(sig, style=style)
    assert a.text == b.text  # byte-identical → golden-file testable


def test_base_intensity_lifts_the_band():
    # A quiet window at a high base energy should still land in a brighter band.
    quiet = _signal(0, 0, 0.0)
    low = compose(quiet, style="Space Dub", base_intensity=0.1)
    high = compose(quiet, style="Space Dub", base_intensity=0.9)
    assert low.brainwave_band == "delta" and high.brainwave_band == "gamma"


def _fast_of(text: str) -> float:
    return float(text.rsplit(".fast(", 1)[1].rstrip(")\n"))


def test_space_dub_tempo_tracks_the_entrainment_carrier():
    # The underlying tempo (.fast) is derived from the band's carrier_hz, so it
    # rises monotonically from delta (2 Hz) to gamma (40 Hz).
    sig = _signal(8, 3)
    fasts = [_fast_of(space_dub.render(sig, 0.5, b)) for b in _BANDS]
    assert fasts == sorted(fasts) and fasts[0] < fasts[-1]


def test_space_dub_uses_the_curated_rhythm_bank():
    # Every render names the genre groove it drew from the 12-pattern bank.
    names = {name for name, _ in space_dub._RHYTHMS}
    used = {space_dub.render(_signal(v, v % 5), 0.5, "alpha").split("groove=")[1].split(" ")[0]
            for v in range(30)}
    assert used <= names and len(used) >= 3  # variety, all from the bank


def test_space_dub_places_notes_only_on_rhythm_onsets():
    # The generator puts pitches on a rhythm's onsets and leaves its rests as rests.
    _, pattern = space_dub._RHYTHMS[8]  # dnb-roll
    seq = space_dub._place(pattern, ("c1", "eb1", "g0"), seed=5)
    tokens = seq.split(" ")
    assert len(tokens) == len(pattern)  # same 16-step grid
    assert sum(t != "~" for t in tokens) == pattern.count("x")  # notes == onsets
    assert tokens[0] == "c1"  # first onset anchored to the root


def test_space_dub_reaches_dnb_for_texture_on_calm_bands():
    # Calm bands lean dub/reggae but the seed sometimes reaches into DnB.
    picks = {space_dub._pick_rhythm(1, seed) for seed in range(40)}
    assert picks & set(space_dub._DNB) and picks & set(space_dub._DUB_REGGAE)


def test_space_dub_syncopation_adds_offbeat_onsets():
    # More off-beat onsets as the amount rises; only off-beat rests get filled.
    pat = space_dub._RHYTHMS[1][1]  # dub-stepper "x.......x......."
    quiet = space_dub._syncopate(pat, seed=3, amount=0.0)
    busy = space_dub._syncopate(pat, seed=3, amount=1.0)
    assert quiet == pat  # amount 0 leaves the groove untouched
    assert busy.count("x") > pat.count("x")  # more onsets when syncopated
    # Quarter-note beats (steps 0,4,8,12) are never overwritten — syncopation is
    # strictly off the beat.
    assert all(busy[i] == pat[i] for i in (0, 4, 8, 12))


def _synco_of(text: str) -> float:
    return float(text.split("synco=")[1].split(" ")[0])


def test_space_dub_syncopation_variable_tracks_volatility():
    calm = space_dub.render(_signal(10, 3, volatility=0.0), 0.5, "theta")
    bursty = space_dub.render(_signal(10, 3, volatility=0.9), 0.5, "theta")
    assert _synco_of(bursty) > _synco_of(calm)


def test_space_dub_chime_evolves_over_long_timescales():
    # The chime must not sit still for hours: long mutually-prime LFOs (sampled
    # from the global clock) + a wandering 8-chord cycle + bar-to-bar A/B stabs.
    text = space_dub.render(_signal(14, 3), 0.5, "theta")
    chime = next(ln for ln in text.splitlines() if "chord(" in ln)
    assert ".slow(31)" in chime and ".slow(23)" in chime and ".slow(29)" in chime
    assert chime.count(" ") > 0 and "<[" in chime  # A/B alternation in the loop
    # eight-chord wandering cycle (longer than the 4-chord bass progression)
    assert any(chord in chime for chord in ("Bbmaj7", "Gmaj7", "Cm11", "Dm11", "Fm11"))
