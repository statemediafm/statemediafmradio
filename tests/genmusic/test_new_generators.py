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
