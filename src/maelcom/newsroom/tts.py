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
