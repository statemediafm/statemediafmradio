"""Tests for composing an ActivitySignal into a StrudelProgram."""

from __future__ import annotations

import re

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


def test_tintinnabuli_is_dark_g_dorian_and_low():
    text = compose(_signal(8, 3, volatility=0.4), style="tintinnabuli").text
    assert "tintinnabuli" in text and text.rstrip().endswith(".slow(2)")  # largo
    assert 'scale("G1:dorian")' in text  # confined to G Dorian
    assert ":minor" not in text  # no minor keys (major/modal is fine)
    # Everything sits in the low octave — no octave-2+ pitch anywhere.
    assert not re.search(r"[A-G]#?[2-9]:", text)


def test_tintinnabuli_low_pass_everywhere_no_harsh_highs():
    text = compose(_signal(8, 3), style="tintinnabuli").text
    cutoffs = [int(x) for x in re.findall(r"\.lpf\((\d+)\)", text)]  # numeric cutoffs
    assert cutoffs and max(cutoffs) <= 700  # nothing bright/harsh (dog-safe)
    assert "sine.range(220,480)" in text  # the pad's slow, low filter LFO


def test_tintinnabuli_voices_come_and_go_and_trade_off():
    text = compose(_signal(8, 3), style="tintinnabuli").text
    assert "[~]" in text  # gated voices leave whole-bar rests — they come and go
    assert text.count(".sustain(0.12)") >= 2  # multiple dark-piano voices (leader/canon/response)


def test_tintinnabuli_melodies_run_double_time_or_greater():
    text = compose(_signal(8, 3, volatility=0.4), style="tintinnabuli").text
    fasts = [int(x) for x in re.findall(r"\.fast\((\d+)\)", text)]  # the melodic voices
    assert fasts and min(fasts) >= 2  # double-time or faster over the slow bed


def test_tintinnabuli_canon_call_response_and_rare_tintinnabuli():
    text = compose(_signal(8, 3), style="tintinnabuli").text
    assert "arrange(" in text and text.rstrip().endswith(").slow(2)")
    assert ".late(" in text  # canon imitation — a voice offset in time
    assert "[6, stack(" in text  # a brief tintinnabuli passage (~every 180 bars)
    assert 's("white")' not in text  # no drums / snare / noise percussion


def test_tintinnabuli_rhythm_evolves_with_turnaround_pause_drop():
    text = compose(_signal(8, 3), style="tintinnabuli").text
    # each ~64-bar movement ends with a turnaround (4), a pause (1) and a drop (3)
    assert "[4, stack(" in text and "[1, stack(" in text and "[3, stack(" in text


def test_tintinnabuli_news_burst_adds_consonant_swell():
    calm = compose(_signal(2, 2), style="tintinnabuli").text  # few news items
    burst = compose(_signal(30, 5), style="tintinnabuli").text  # a burst of news
    assert "[0,2,4]" not in calm  # baseline: no accent
    assert "[0,2,4]" in burst  # a consonant tonic-triad swell (in-key) on a burst


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
