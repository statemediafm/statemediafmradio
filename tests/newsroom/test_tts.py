"""Tests for the dependency-free tone TTS default and voice resolution."""

from __future__ import annotations

import pytest

from statemediafm.core.models import AudioRef, Script
from statemediafm.newsroom.tts import (
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


def test_render_reads_brackets_the_headline_block_with_pauses():
    tts = ToneWavTTS()
    reads = [("other", "Intro."), ("headline", "One."), ("headline", "Two."), ("other", "Bye.")]
    tight = render_reads(reads, tts, headline_pause_ms=0)
    spaced = render_reads(reads, tts, headline_pause_ms=1000)
    assert spaced.data[:4] == b"RIFF"
    # Three ~1s gaps: before the first headline, between the two, and after the
    # last (before the sign-off) → ~3s longer.
    assert spaced.duration_ms - tight.duration_ms >= 2700


def test_render_reads_pause_reads_add_extra_silence():
    tts = ToneWavTTS()
    base = [("other", "Ident."), ("other", "Body.")]
    withpause = [("other", "Ident."), ("pause", "", "2"), ("other", "Body.")]
    plain = render_reads(base, tts, headline_pause_ms=1000)
    paused = render_reads(withpause, tts, headline_pause_ms=1000)
    # a "pause" read with multiplier 2 inserts ~2s of extra silence
    assert paused.duration_ms - plain.duration_ms >= 1900


def test_render_reads_requires_content():
    with pytest.raises(ValueError):
        render_reads([], ToneWavTTS())


def test_render_reads_accepts_fractional_pause_multiplier():
    from statemediafm.newsroom.summarize import Read
    from statemediafm.newsroom.tts import ToneWavTTS, render_reads

    reads = [Read("other", "one"), Read("pause", "", "0.5"), Read("other", "two")]
    audio = render_reads(reads, ToneWavTTS(), headline_pause_ms=1000)
    # 0.5 * 1000 ms = 500 ms of inserted silence between the two spoken clips.
    assert audio.duration_ms >= 500


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
