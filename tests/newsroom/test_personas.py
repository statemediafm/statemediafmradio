"""Tests for the themed voice personas."""

from __future__ import annotations

from maelcom.newsroom.personas import PERSONAS, get_persona, persona_names
from maelcom.newsroom.tts import voice_names


def test_curated_personas_present():
    names = persona_names()
    assert {"BBC World", "John Peel", "Public Radio"} <= set(names)


def test_get_persona_returns_none_for_custom_or_unknown():
    assert get_persona(None) is None
    assert get_persona("Custom") is None
    assert get_persona("Nope") is None


def test_persona_voices_are_real_piper_voices():
    # Every persona must name a voice the TTS actually offers, or it can't air.
    voices = set(voice_names())
    for p in PERSONAS.values():
        assert p.voice in voices, p.name
        assert p.style and p.ident and p.signoff  # fully specified
