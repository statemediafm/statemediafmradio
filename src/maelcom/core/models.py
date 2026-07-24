"""Shared data model — the contracts every pillar speaks (plan §6).

M1 subset: ``NewsItem``, ``Script``, ``AudioRef``, ``Segment``,
``BroadcastPlan``. The remaining types (``ActivitySignal``, ``StrudelProgram``,
``SongCue``) land alongside the pillars that produce them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True, slots=True)
class NewsItem:
    """A normalized unit of activity from a source platform.

    ``acl`` carries whatever access-control metadata the source exposes so it
    can be enforced downstream; ``raw`` keeps the untouched source payload for
    debugging and richer summarization later.
    """

    id: str
    source: str
    title: str
    body: str = ""
    kind: str = "update"
    tenant: str | None = None
    actors: list[str] = field(default_factory=list)
    timestamp: datetime | None = None
    refs: list[str] = field(default_factory=list)
    acl: Any = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Script:
    """A voiced-news unit: the words to read, plus how to read them.

    ``segments`` is for later multi-voice splitting (one entry per voice turn);
    in the M1 single-voice slice it stays empty and ``text`` holds the whole
    read.
    """

    text: str
    style: str
    voice: str | None = None
    segments: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class AudioRef:
    """A rendered audio clip. For M1 the bytes are held in-memory (``data``);
    later this can become a URL/object-store reference instead."""

    id: str
    media_type: str = "audio/wav"
    data: bytes = b""
    duration_ms: int = 0


@dataclass(frozen=True, slots=True)
class Segment:
    """One timed entry in a broadcast plan."""

    kind: str  # "news" | "music" | "song"
    start_s: float
    duration_s: float
    audio: AudioRef | None = None
    script: Script | None = None


@dataclass(frozen=True, slots=True)
class BroadcastPlan:
    """An ordered, timed list of segments for a tenant over a window."""

    segments: list[Segment] = field(default_factory=list)
    tenant: str | None = None
    window_start: datetime | None = None
    window_end: datetime | None = None
