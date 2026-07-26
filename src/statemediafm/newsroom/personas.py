"""Themed voice personas: a named bundle of writing style + voice + phrasing.

A **persona** ties together the three levers that give a broadcast its character
— the writing *style* (a prompt hint for the summarizer), the speaking *voice* (a
Piper voice), and the *phrasing* (the station ident and sign-off lines). Selecting
one in the Settings tab sets all three at once, so the news sounds like a station
rather than a generic feed (plan §5.2 / M4). ``Custom`` keeps the free-form
style/voice controls and the default phrasing.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..licensing import register_module

# Themed voice personas are a **commercial module** (open-core): the base station
# ships the free Custom style/voice controls; personas require a license key. The
# enable-points (the /persona endpoints) guard on ``entitled(MODULE)``.
MODULE = "voice-personas"
register_module(
    MODULE,
    "Themed voice personas",
    "Curated on-air identities (BBC World, John Peel, Public Radio) bundling a "
    "writing style, voice and station phrasing.",
)

# The default (Custom) phrasing — the firmwide-radio wording used when no persona
# is selected. Personas override these; radio_reads falls back to them.
DEFAULT_IDENT = "This is the firmwide radio service."
DEFAULT_SIGNOFF = "And that's the current state. More as things develop."


@dataclass(frozen=True, slots=True)
class Persona:
    """One on-air identity: what to write like, who reads it, and how it's topped
    and tailed. ``voice`` is a friendly Piper voice name (see ``newsroom.tts``)."""

    name: str
    style: str  # writing-style hint fed to the summarizer / prompt
    voice: str  # Piper voice (friendly alias)
    ident: str  # the station-ident line, read after the opener
    signoff: str  # the closing line


# Curated starting set (plan M4). Each pairs a distinct writing style with a
# distinct voice and its own ident/sign-off cadence.
PERSONAS: dict[str, Persona] = {
    "BBC World": Persona(
        name="BBC World",
        style="bbc-world",
        voice="alan",
        ident="This is the world service.",
        signoff="That is the latest from the newsroom. Do stay with us.",
    ),
    "John Peel": Persona(
        name="John Peel",
        style="john-peel late-night, wry and warm",
        voice="northern_english_male",
        ident="You're listening to the graveyard shift.",
        signoff="That's your lot for now. Keep it Peel.",
    ),
    "Public Radio": Persona(
        name="Public Radio",
        style="npr public-radio, measured and considered",
        voice="southern_english_female",
        ident="This is your community radio service.",
        signoff="And that's where we'll leave it. More as it develops.",
    ),
}


def persona_names() -> list[str]:
    """The selectable persona names, in display order."""
    return list(PERSONAS)


def get_persona(name: str | None) -> Persona | None:
    """The :class:`Persona` for ``name``, or ``None`` (Custom / unknown)."""
    return PERSONAS.get(name) if name else None
