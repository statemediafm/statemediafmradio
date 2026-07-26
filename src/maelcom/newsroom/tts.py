"""Text-to-speech provider interface + a dependency-free default.

The default ``ToneWavTTS`` synthesizes a short, quiet tone sized to the script's
estimated read time — no binaries, no network, deterministic — so the vertical
slice produces a real, playable ``AudioRef`` in CI and in the zero-config demo.
Real speech is ``PiperTTS`` (offline neural TTS via the ``piper-tts`` package,
an opt-in ``[tts]`` extra); cloud voices are further adapters behind the same
interface.
"""

from __future__ import annotations

import hashlib
import io
import math
import os
import struct
import urllib.request
import wave
from abc import ABC, abstractmethod
from pathlib import Path

from ..core.models import AudioRef, Script

_WORDS_PER_MINUTE = 150

_VOICE_HUB = "https://huggingface.co/rhasspy/piper-voices/resolve/main"
# Piper's voice files live under <lang_region>/<lang_region>/<name>/<quality>/.
# e.g. en/en_GB/alan/medium/en_GB-alan-medium.onnx(.json)
_VOICE_PATHS = {
    "en_GB-alan-medium": "en/en_GB/alan/medium",  # British male
    "en_GB-alba-medium": "en/en_GB/alba/medium",  # British (Scottish) female
    "en_GB-northern_english_male-medium": "en/en_GB/northern_english_male/medium",
    "en_GB-southern_english_female-low": "en/en_GB/southern_english_female/low",
    "en_US-amy-low": "en/en_US/amy/low",
    "en_US-lessac-medium": "en/en_US/lessac/medium",
}

# Friendly short names for the curated British voices (what --voice accepts).
_VOICE_ALIASES = {
    "alan": "en_GB-alan-medium",
    "alba": "en_GB-alba-medium",
    "northern_english_male": "en_GB-northern_english_male-medium",
    "southern_english_female": "en_GB-southern_english_female-low",
}

# Default offline voice: Alan, a British male (accepts the alias below too).
_DEFAULT_VOICE = "alan"


def _assemble_wavs(clips: list[AudioRef], gaps_ms: list[int]) -> AudioRef:
    """Join WAV clips with a per-clip *leading* silence (``gaps_ms[i]`` before
    clip ``i``; the first clip's gap is ignored). All clips must share format."""
    pairs = [(g, c) for g, c in zip(gaps_ms, clips) if c and c.data]
    if not pairs:
        raise ValueError("concat: nothing to assemble")

    params: tuple[int, int, int] | None = None
    frames = bytearray()
    for i, (gap, ref) in enumerate(pairs):
        with wave.open(io.BytesIO(ref.data)) as w:
            fmt = (w.getnchannels(), w.getsampwidth(), w.getframerate())
            data = w.readframes(w.getnframes())
        if params is None:
            params = fmt
        elif fmt != params:
            raise ValueError(f"concat: format mismatch at segment {i}: {fmt} != {params}")
        if i and gap > 0:
            frames += b"\x00" * int(params[0] * params[1] * params[2] * gap / 1000)
        frames += data

    nchannels, width, rate = params  # type: ignore[misc]
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(nchannels)
        w.setsampwidth(width)
        w.setframerate(rate)
        w.writeframes(bytes(frames))
    out = buf.getvalue()
    return AudioRef(
        id=hashlib.sha256(out).hexdigest()[:16],
        media_type="audio/wav",
        data=out,
        duration_ms=round(len(frames) / (rate * width * nchannels) * 1000),
    )


def concat_wavs(refs: list[AudioRef], gap_ms: int = 400) -> AudioRef:
    """Concatenate WAV ``AudioRef``s into one, with ``gap_ms`` silence between.

    All clips must share the same format (channels, sample width, rate) — true
    for any set produced by one TTS voice in a run. Raises ``ValueError`` on an
    empty list or a format mismatch. This is how a multi-segment broadcast
    becomes a single playable file.
    """
    usable = [r for r in refs if r and r.data]
    if not usable:
        raise ValueError("concat_wavs: nothing to concatenate")
    return _assemble_wavs(usable, [0] + [gap_ms] * (len(usable) - 1))


def render_reads(
    reads: list,
    tts: TTSProvider,
    *,
    style: str = "",
    voice: str | None = None,
    headline_pause_ms: int = 1000,
    voice_for: object = None,
) -> AudioRef:
    """Voice reads into one clip, pausing between headlines and switching voice.

    Each read is ``(role, text[, origin])``. A read is voiced with ``tts``,
    except a ``"headline"`` whose ``origin`` maps to another provider via
    ``voice_for(origin)`` — so headlines from different sources speak in
    different voices (all must share audio format to concatenate). A
    ``headline_pause_ms`` silence brackets the headline block — before the first
    headline, between headlines, and after the last (before the sign-off). A
    ``"pause"`` read (text empty, ``origin`` = a multiplier string) inserts that
    many extra ``headline_pause_ms`` beats of silence at that point.
    Raises ``ValueError`` if nothing is voiceable.
    """
    clips: list[AudioRef] = []
    gaps: list[int] = []
    prev_role: str | None = None
    extra = 0  # pending extra silence contributed by "pause" reads
    for read in reads:
        role, text = read[0], read[1]
        origin = read[2] if len(read) > 2 else None
        if role == "pause":  # not voiced; adds silence before the next read
            extra += int(origin or 1) * headline_pause_ms
            continue
        text = text.strip()
        if not text:
            continue
        provider = tts
        if role == "headline" and voice_for is not None and origin is not None:
            provider = voice_for(origin) or tts
        # Bracket the headline block (before first, between, after last), plus any
        # pending extra pause.
        gap = (headline_pause_ms if "headline" in (role, prev_role) else 0) + extra
        extra = 0
        clips.append(provider.render(Script(text=text, style=style), voice=voice))
        gaps.append(gap)
        prev_role = role
    if not clips:
        raise ValueError("render_reads: no non-empty reads")
    gaps[0] = 0  # no leading silence
    return _assemble_wavs(clips, gaps)


class TTSProvider(ABC):
    """Render a Script to audio."""

    @abstractmethod
    def render(self, script: Script, voice: str | None = None) -> AudioRef:
        raise NotImplementedError


class ToneWavTTS(TTSProvider):
    """Placeholder voice: a low sine tone whose length matches the read time.

    Stands in for real speech so the pipeline, plan, and player can be exercised
    end to end without a speech engine. Deterministic: the same script always
    yields the same bytes.
    """

    def __init__(self, sample_rate: int = 8000, frequency: float = 220.0) -> None:
        self.sample_rate = sample_rate
        self.frequency = frequency

    def render(self, script: Script, voice: str | None = None) -> AudioRef:
        words = max(1, len(script.text.split()))
        duration_s = max(1.0, words / _WORDS_PER_MINUTE * 60.0)
        wav_bytes = self._tone(duration_s)
        clip_id = hashlib.sha256(
            f"{voice or ''}:{script.text}".encode()
        ).hexdigest()[:16]
        return AudioRef(
            id=clip_id,
            media_type="audio/wav",
            data=wav_bytes,
            duration_ms=round(duration_s * 1000),
        )

    def _tone(self, duration_s: float) -> bytes:
        n = int(duration_s * self.sample_rate)
        amp = 6000  # low amplitude of int16 range
        frames = bytearray()
        for i in range(n):
            sample = int(amp * math.sin(2 * math.pi * self.frequency * i / self.sample_rate))
            frames += struct.pack("<h", sample)
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(self.sample_rate)
            w.writeframes(bytes(frames))
        return buf.getvalue()


def default_voices_dir() -> Path:
    """Where downloaded Piper voice models are cached.

    ``MAELCOM_VOICES_DIR`` overrides; otherwise ``./voices`` under the cwd.
    """
    return Path(os.environ.get("MAELCOM_VOICES_DIR", "voices"))


def resolve_piper_voice(
    voice: str = _DEFAULT_VOICE, voices_dir: Path | None = None
) -> tuple[Path, Path]:
    """Return local ``(model.onnx, model.onnx.json)`` paths for ``voice``.

    Downloads the pair from the Piper voice hub into ``voices_dir`` the first
    time (network required once); subsequent calls are offline. ``voice`` may
    be a friendly alias (``alan``, ``alba``, ``northern_english_male``,
    ``southern_english_female``), a full Piper name (see ``_VOICE_PATHS``), or a
    filesystem path to a ``.onnx`` model.
    """
    # Explicit path to a model file.
    p = Path(voice)
    if p.suffix == ".onnx" and p.exists():
        return p, p.with_suffix(".onnx.json")

    voice = _VOICE_ALIASES.get(voice, voice)  # resolve friendly aliases
    if voice not in _VOICE_PATHS:
        known = sorted(_VOICE_ALIASES) + sorted(_VOICE_PATHS)
        raise ValueError(
            f"Unknown voice {voice!r}. Known: {', '.join(known)}, "
            "or pass a path to a local .onnx model."
        )

    voices_dir = voices_dir or default_voices_dir()
    voices_dir.mkdir(parents=True, exist_ok=True)
    model = voices_dir / f"{voice}.onnx"
    config = voices_dir / f"{voice}.onnx.json"
    remote = f"{_VOICE_HUB}/{_VOICE_PATHS[voice]}"
    for local, name in ((model, model.name), (config, config.name)):
        if not local.exists():
            urllib.request.urlretrieve(f"{remote}/{name}?download=true", local)
    return model, config


class PiperTTS(TTSProvider):
    """Offline neural speech via Piper — the real newsroom voice.

    Loads a Piper voice model once and synthesizes each script to a WAV.
    Requires the ``[tts]`` extra (``piper-tts``); the voice model is resolved
    (and downloaded on first use) by ``resolve_piper_voice``.
    """

    def __init__(self, voice: str = _DEFAULT_VOICE, voices_dir: Path | None = None) -> None:
        from piper import PiperVoice  # lazy: only needed when Piper is used

        model, config = resolve_piper_voice(voice, voices_dir)
        self.voice_name = voice
        self._voice = PiperVoice.load(str(model), str(config))

    def render(self, script: Script, voice: str | None = None) -> AudioRef:
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            self._voice.synthesize_wav(script.text, w)
        data = buf.getvalue()
        with wave.open(io.BytesIO(data)) as r:
            duration_ms = round(r.getnframes() / r.getframerate() * 1000)
        clip_id = hashlib.sha256(
            f"{self.voice_name}:{voice or ''}:{script.text}".encode()
        ).hexdigest()[:16]
        return AudioRef(
            id=clip_id,
            media_type="audio/wav",
            data=data,
            duration_ms=duration_ms,
        )
