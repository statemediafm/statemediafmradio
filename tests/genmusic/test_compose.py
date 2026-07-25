"""Tests for composing an ActivitySignal into a StrudelProgram."""

from __future__ import annotations

import pytest

from maelcom.core.models import ActivitySignal, StrudelProgram
from maelcom.genmusic.compose import compose


def _signal(volume: int, participants: int, volatility: float = 0.2) -> ActivitySignal:
    voices = {f"dev{i}": v for i, v in enumerate(["rhodes", "upright-bass", "vibraphone"][:participants])}
    return ActivitySignal(
        window_s=3600.0,
        volume=volume,
        volatility=volatility,
        participant_count=participants,
        themes=["scheduler", "telemetry"],
        actor_voices=voices,
    )


def test_compose_returns_program_with_matching_band():
    program = compose(_signal(3, 2))
    assert isinstance(program, StrudelProgram)
    assert program.style == "lofi"
    assert 0.0 <= program.intensity <= 1.0
    # The band is exactly the one the intensity falls in.
    from maelcom.genmusic.brainwave import band_for_intensity

    assert program.brainwave_band == band_for_intensity(program.intensity)


def test_unknown_style_raises():
    with pytest.raises(ValueError):
        compose(_signal(3, 2), style="space-dub")


def test_intensity_override_is_respected():
    program = compose(_signal(3, 2), intensity=0.9)
    assert program.intensity == 0.9
    assert program.brainwave_band == "gamma"


def test_quiet_repo_idles_low_busy_repo_energizes():
    quiet = compose(_signal(1, 1, volatility=0.0))
    busy = compose(_signal(50, 8, volatility=0.9))
    assert quiet.intensity < busy.intensity
    assert quiet.brainwave_band in {"delta", "theta"}
    assert busy.brainwave_band in {"beta", "gamma"}


def test_program_text_is_valid_looking_strudel():
    text = compose(_signal(20, 5, volatility=0.5)).text
    assert "setcps(" in text
    assert "stack(" in text and ").fadeIn(" in text
    # Metadata appears only in the leading header comment — never inline, so it
    # can't comment out a layer-separating comma.
    lines = text.splitlines()
    assert lines[0].startswith("// maelcom lofi")
    for line in lines[1:]:
        assert "//" not in line


def test_compose_is_deterministic():
    a = compose(_signal(12, 3, volatility=0.4)).text
    b = compose(_signal(12, 3, volatility=0.4)).text
    assert a == b


def test_rhythm_layers_are_always_present():
    # A plucky pentatonic melody and synth hats sound without any samples, so
    # there is audible rhythm even for a quiet, single-participant repo.
    solo = compose(_signal(1, 1, volatility=0.0)).text
    assert "scale(" in solo  # melody
    assert "c5*" in solo  # synth hats


def test_hats_get_denser_with_intensity():
    calm = compose(_signal(2, 1), intensity=0.2).text
    busy = compose(_signal(40, 8, volatility=0.9), intensity=0.9).text
    assert "c5*4" in calm
    assert "c5*8" in busy
