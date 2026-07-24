"""Tests for the dependency-free tone TTS default."""

from __future__ import annotations

from maelcom.core.models import AudioRef, Script
from maelcom.newsroom.tts import ToneWavTTS


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
