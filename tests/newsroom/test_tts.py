"""Tests for the dependency-free tone TTS default and voice resolution."""

from __future__ import annotations

import pytest

from maelcom.core.models import AudioRef, Script
from maelcom.newsroom.tts import (
    _VOICE_ALIASES,
    _VOICE_PATHS,
    ToneWavTTS,
    concat_wavs,
    render_reads,
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


def test_render_reads_pauses_only_between_headlines():
    tts = ToneWavTTS()
    reads = [("other", "Intro."), ("headline", "One."), ("headline", "Two."), ("other", "Bye.")]
    tight = render_reads(reads, tts, headline_pause_ms=0)
    spaced = render_reads(reads, tts, headline_pause_ms=1000)
    assert spaced.data[:4] == b"RIFF"
    # Exactly one gap (between the two consecutive headlines) → ~1s longer.
    assert spaced.duration_ms - tight.duration_ms >= 900


def test_render_reads_requires_content():
    with pytest.raises(ValueError):
        render_reads([], ToneWavTTS())


def test_render_reads_switches_voice_per_origin():
    base = ToneWavTTS()
    other = ToneWavTTS(frequency=180.0)  # same 8 kHz format → concatenates
    consulted: list[str] = []

    def voice_for(origin):
        consulted.append(origin)
        return other if origin == "meltano" else None

    reads = [
        ("other", "Intro."),
        ("headline", "Opus 5.", "Hacker News"),
        ("headline", "Fix the bug.", "meltano"),
    ]
    out = render_reads(reads, base, headline_pause_ms=0, voice_for=voice_for)
    assert out.data[:4] == b"RIFF"
    # voice_for is consulted for each headline's origin (not the 'other' read).
    assert consulted == ["Hacker News", "meltano"]
