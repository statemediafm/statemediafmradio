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


def test_space_dub_uses_curated_riffs_from_the_genre_banks():
    # Every render names its genre; all three banks appear across signals, and the
    # bass is a curated riff (key-relative degrees), not random note placement.
    used = {space_dub.render(_signal(v, v % 5), 0.5, b).split("groove=")[1].split(" ")[0]
            for v in range(30) for b in ("theta", "gamma")}
    assert used <= {"dub", "reggae", "dnb"} and len(used) == 3
    text = space_dub.render(_signal(8, 3), 0.4, "theta")
    assert any(riff.replace('"', "") in text  # the placed riff is a curated one
               for pool in space_dub._RIFFS.values() for riff in pool)


def test_space_dub_wires_the_dub_building_blocks():
    # Drum machine (samples), bass synth (round + key-relative riff), lead skank
    # with delay — the classic dub kit, all present.
    text = space_dub.render(_signal(10, 3), 0.4, "theta")
    assert 's("bd")' in text and 's("sd")' in text and 's("hh")' in text  # drum machine
    assert '.scale("c1:minor:pentatonic").s("triangle")' in text  # deep bass synth
    assert '"~ x ~ x ~ x ~ x"' in text and ".delay(" in text  # off-beat skank + delay


def _synco_of(text: str) -> float:
    return float(text.split("synco=")[1].split(" ")[0])


def test_space_dub_syncopation_variable_tracks_volatility():
    calm = space_dub.render(_signal(10, 3, volatility=0.0), 0.5, "theta")
    bursty = space_dub.render(_signal(10, 3, volatility=0.9), 0.5, "theta")
    assert _synco_of(bursty) > _synco_of(calm)


@pytest.mark.parametrize("band", _BANDS)
def test_space_dub_emits_only_verified_primitives(band):
    # The reliability guarantee: built on the IR, Space Dub can only emit methods
    # confirmed to sound in @strudel/web 1.0.3 — never an unverified one.
    from statemediafm.genmusic.ir import VERIFIED_METHODS, used_methods

    text = space_dub.render(_signal(12, 3), 0.5, band)
    assert used_methods(text) <= VERIFIED_METHODS
    for bad in ("swingBy", "lpenv", "lpq("):
        assert bad not in text


def test_space_dub_pad_evolves_over_long_timescales():
    # The atmosphere pad must not sit still for hours: long mutually-prime LFOs
    # (sampled from the global clock) on a wandering 8-chord cycle.
    text = space_dub.render(_signal(14, 3), 0.5, "theta")
    pad = next(ln for ln in text.splitlines() if ".slow(31)" in ln)
    assert ".slow(31)" in pad and ".slow(23)" in pad and ".slow(29)" in pad
    # eight-chord wandering cycle (longer than the 4-chord skank progression)
    assert any(chord in pad for chord in ("Bbmaj7", "Gmaj7", "Cm11", "Dm11", "Fm11"))
