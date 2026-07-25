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
    assert program.style == "tintinnabuli"  # the default style
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
    text = compose(_signal(20, 5, volatility=0.5), style="lofi").text
    assert "stack(" in text and ").fast(" in text
    # @strudel/web has neither, and using them makes the whole program fail.
    assert "setcps" not in text and "fadeIn" not in text
    # Metadata appears only in the leading header comment — never inline, so it
    # can't comment out a layer-separating comma.
    lines = text.splitlines()
    assert lines[0].startswith("// maelcom lofi")
    for line in lines[1:]:
        assert "//" not in line


def test_no_style_uses_unsupported_strudel_functions():
    # Guard every style against the setcps/fadeIn trap that silently kills audio.
    for style in ("tintinnabuli", "lofi"):
        text = compose(_signal(8, 3, volatility=0.4), style=style).text
        assert "setcps" not in text and "fadeIn" not in text, style


def test_tintinnabuli_has_m_and_t_voices_largo_piano():
    text = compose(_signal(8, 3, volatility=0.4), style="tintinnabuli").text
    assert "tintinnabuli" in text and text.rstrip().endswith(".slow(2)")  # largo
    # Two modified-piano voices (sawtooth + piano amplitude ADSR) + sawtooth lead.
    assert text.count(".sustain(0.08)") == 2  # M-voice and T-voice
    assert 's("sawtooth")' in text  # piano voices and the lead
    assert 'scale("A3:minor")' in text


def test_tintinnabuli_dissonance_scales_with_news_volume():
    calm = compose(_signal(2, 2), style="tintinnabuli").text  # few news items
    burst = compose(_signal(30, 5), style="tintinnabuli").text  # a burst of news
    assert "d#5" not in calm  # consonant baseline — no dissonant stab
    assert "d#5" in burst  # dissonance accent enters with a burst of news


def test_tintinnabuli_softens_high_notes_via_note_shaping():
    text = compose(_signal(8, 3), style="tintinnabuli").text
    # White noise is part of the high notes: a transient following the note's
    # ADSR (short attack/decay/sustain), not a reverb drone.
    assert 's("white").hpf(1500)' in text
    assert ".attack(0.004).decay(0.28)" in text  # note-shaped noise transient
    assert "roomsize(6)" not in text  # no big reverb wash
    # A filter envelope shapes the ADSR filter of the >C3 voices.
    assert ".lpenv(" in text


def test_no_triangle_or_square_above_c2():
    # Timbre rule: triangle/square are reserved for low (<=C2), short sounds; the
    # styles here have no such notes, so they use none at all.
    for style in ("tintinnabuli", "lofi"):
        text = compose(_signal(8, 3, volatility=0.4), style=style).text
        assert '"triangle"' not in text and '"square"' not in text, style


def test_compose_is_deterministic():
    a = compose(_signal(12, 3, volatility=0.4)).text
    b = compose(_signal(12, 3, volatility=0.4)).text
    assert a == b


def test_synth_beat_is_always_present():
    # The lofi beat is synthesized (sine kick, noise snare + hats, plucky
    # melody), so it sounds without samples even for a quiet, solo repo.
    solo = compose(_signal(1, 1, volatility=0.0), style="lofi").text
    assert "scale(" in solo  # melody
    assert '.s("sine")' in solo  # kick
    assert 's("white").struct' in solo  # snare on the backbeat
    assert 's("white*' in solo  # hats
    assert '"bd' not in solo and "RolandTR909" not in solo  # no sample drums


def test_hats_get_denser_with_intensity():
    calm = compose(_signal(2, 1), intensity=0.2, style="lofi").text
    busy = compose(_signal(40, 8, volatility=0.9), intensity=0.9, style="lofi").text
    assert "white*4" in calm
    assert "white*8" in busy
