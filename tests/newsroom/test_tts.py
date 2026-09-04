"""Tests for the dependency-free tone TTS default and voice resolution."""

from __future__ import annotations

import pytest

from statemediafm.core.models import AudioRef, Script
from statemediafm.newsroom.tts import (
    _VOICE_ALIASES,
    _VOICE_PATHS,
    _VOICE_URLS,
    ToneWavTTS,
    concat_wavs,
    render_reads,
    resolve_piper_voice,
    voice_names,
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


def test_community_voices_are_offered_and_url_backed():
    # The brycebeattie.com additions show up in the picker alongside the curated set.
    names = voice_names()
    for v in ("norman", "cori_medium", "jenny_dioco", "bryce"):
        assert v in names and v in _VOICE_URLS
        onnx, cfg = _VOICE_URLS[v]
        assert onnx.startswith("https://") and onnx.endswith(".onnx")
        assert cfg == onnx + ".json"


def test_random_rotation_excludes_the_community_voices():
    from statemediafm.newsroom.tts import rotation_voice_names
    from statemediafm.serve import _voice_rotation

    # The auto "Random" rotation draws only from the original curated set...
    rot = rotation_voice_names()
    assert set(rot) == set(_VOICE_ALIASES)
    assert not (set(rot) & set(_VOICE_URLS))  # no community voices
    # ...even though the picker still offers them.
    assert set(_VOICE_URLS) <= set(voice_names())

    # An unpinned rotation (base 'alan') cycles only curated voices.
    assert set(_voice_rotation("alan")) == set(_VOICE_ALIASES)
    # A community base voice still leads, with the curated set behind it (opt-in lead).
    order = _voice_rotation("jenny_dioco")
    assert order[0] == "jenny_dioco" and set(order[1:]) == set(_VOICE_ALIASES)


def test_resolve_community_voice_downloads_from_its_urls(tmp_path, monkeypatch):
    # A URL-backed voice fetches its .onnx/.onnx.json pair (no hub path), caching
    # them in the voices dir; a second resolve is offline.
    from statemediafm.newsroom import tts

    calls = []

    def _fake_urlretrieve(url, dest):
        calls.append(url)
        dest = str(dest)
        with open(dest, "wb") as f:
            f.write(b"stub")

    monkeypatch.setattr(tts.urllib.request, "urlretrieve", _fake_urlretrieve)
    model, config = resolve_piper_voice("jenny_dioco", voices_dir=tmp_path)
    assert model.name == "jenny_dioco.onnx" and config.name == "jenny_dioco.onnx.json"
    assert calls == [
        "https://sfo3.digitaloceanspaces.com/bkmdls/jenny.onnx",
        "https://sfo3.digitaloceanspaces.com/bkmdls/jenny.onnx.json",
    ]
    calls.clear()
    resolve_piper_voice("jenny_dioco", voices_dir=tmp_path)  # cached → no downloads
    assert calls == []


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


def test_render_reads_fractional_pause_keeps_22050hz_frame_aligned():
    # A fractional pause must insert whole frames of silence — otherwise the clip
    # after it is byte-shifted and reads back as white noise (regression: 0.45 s at
    # 22050 Hz mono 16-bit = 19845 bytes, odd). Piper's voices are 22050 Hz.
    import io
    import struct
    import wave

    from statemediafm.core.models import AudioRef
    from statemediafm.newsroom.summarize import Read
    from statemediafm.newsroom.tts import TTSProvider, render_reads

    def _clip(value, n=2205):  # 0.1 s of a constant sample at 22050 Hz
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(22050)
            w.writeframes(struct.pack("<" + "h" * n, *([value] * n)))
        return AudioRef(id="x", media_type="audio/wav", data=buf.getvalue(), duration_ms=100)

    class _Fake(TTSProvider):
        def render(self, script, voice=None):
            return _clip(1000)

    reads = [Read("other", "a"), Read("pause", "", "0.45"), Read("other", "b")]
    audio = render_reads(reads, _Fake(), headline_pause_ms=1000)
    with wave.open(io.BytesIO(audio.data)) as w:
        frames = w.readframes(w.getnframes())
    samples = struct.unpack("<" + "h" * (len(frames) // 2), frames)
    # Every non-silent sample must still be exactly 1000 — a byte shift would turn
    # them into garbage (white noise).
    assert {s for s in samples if s != 0} == {1000}


def test_piper_render_falls_back_to_silence_when_synth_is_empty():
    # A voice whose synth writes nothing (or fails) must yield a valid silent clip,
    # not crash — a single odd read previously 500'd the whole bulletin.
    import wave

    from statemediafm.core.models import Script
    from statemediafm.newsroom.tts import PiperTTS

    tts = PiperTTS.__new__(PiperTTS)  # bypass __init__ (no model download)

    class _NoOpVoice:
        class config:
            sample_rate = 22050

        def synthesize_wav(self, text, w):
            pass  # writes nothing → an invalid WAV without the guard

    tts._loaded = {"x": _NoOpVoice()}
    tts.voice_name = "x"
    tts._voices_dir = None
    ref = tts.render(Script(text="anything", style="s"))
    with wave.open(__import__("io").BytesIO(ref.data)) as r:
        assert r.getframerate() == 22050 and r.getnframes() > 0  # real, playable silence


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
