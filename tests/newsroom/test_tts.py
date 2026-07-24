"""Tests for the dependency-free tone TTS default and voice resolution."""

from __future__ import annotations

import pytest

from maelcom.core.models import AudioRef, Script
from maelcom.newsroom.tts import (
    _VOICE_ALIASES,
    _VOICE_PATHS,
    ToneWavTTS,
    concat_wavs,
    resolve_piper_voice,
)


def test_render_produces_playable_wav():
    tts = ToneWavTTS()
    script = Script(text="one two three four five", style="lofi")
    ref = tts.render(script)
    assert isinstance(ref, AudioRef)
    assert ref.media_type == "audio/wav"
    assert ref.data[:4] == b"RIFF"  # valid WAV header
    assert ref.duration_ms > 0


def test_render_is_deterministic():
    tts = ToneWavTTS()
    script = Script(text="alpha beta gamma", style="lofi")
    assert tts.render(script).id == tts.render(script).id
    assert tts.render(script).data == tts.render(script).data


def test_longer_script_is_longer_audio():
    tts = ToneWavTTS()
    short = tts.render(Script(text="one two", style="x"))
    long = tts.render(Script(text="one two " * 40, style="x"))
    assert long.duration_ms > short.duration_ms


def test_curated_voice_aliases_map_to_known_models():
    for alias in ("alan", "alba", "northern_english_male", "southern_english_female"):
        assert alias in _VOICE_ALIASES
        assert _VOICE_ALIASES[alias] in _VOICE_PATHS


def test_resolve_rejects_unknown_voice_without_network():
    # A non-.onnx, non-alias, non-name string fails fast (no download attempted).
    with pytest.raises(ValueError, match="Unknown voice"):
        resolve_piper_voice("definitely-not-a-voice")


def test_concat_wavs_joins_clips_with_gap():
    tts = ToneWavTTS()
    a = tts.render(Script(text="one two three", style="x"))
    b = tts.render(Script(text="four five", style="x"))
    combined = concat_wavs([a, b], gap_ms=500)
    assert combined.data[:4] == b"RIFF"
    # Combined length ≈ a + b + the 500 ms gap (allow rounding slack).
    assert combined.duration_ms >= a.duration_ms + b.duration_ms + 400


def test_concat_wavs_rejects_empty_and_format_mismatch():
    with pytest.raises(ValueError, match="nothing to concatenate"):
        concat_wavs([])
    a = ToneWavTTS(sample_rate=8000).render(Script(text="x", style="x"))
    b = ToneWavTTS(sample_rate=16000).render(Script(text="y", style="x"))
    with pytest.raises(ValueError, match="format mismatch"):
        concat_wavs([a, b])
