"""Shared data model — the contracts every pillar speaks (plan §6).

M1 subset: ``NewsItem``, ``Script``, ``AudioRef``, ``Segment``,
``BroadcastPlan``. M2 adds the generative-music types ``ActivitySignal`` and
``StrudelProgram``. ``SongCue`` (M5) lands with the music pillar.
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
    # Human-readable origin for on-air attribution ("Hacker News", "meltano").
    # ``source`` is the pillar/source name ("git", "forge", "hackernews");
    # ``origin`` names the specific place a headline came from.
    origin: str | None = None
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
class ActivitySignal:
    """Windowed features of the news stream, driving the generative music.

    ``volume`` is the item count in the window; ``volatility`` (0..1) captures
    how bursty the activity is (evenly spaced → low, clustered → high);
    ``actor_voices`` maps a participant to a named voice/theme so the music can
    reflect who is active. All fields are derived deterministically from a
    ``NewsItem`` window (see ``genmusic.activity``).
    """

    window_s: float
    volume: int
    volatility: float
    participant_count: int
    themes: list[str] = field(default_factory=list)
    actor_voices: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class StrudelProgram:
    """A generative-music program as Strudel source text (the transport).

    No audio is rendered server-side — the client plays ``text`` and crossfades
    over ``fade_ms`` between polls. ``intensity`` (0..1) and ``brainwave_band``
    (delta/theta/alpha/beta/gamma) describe the felt energy; sessions start at
    theta and adapt upward with activity (plan §5.3).
    """

    text: str
    style: str
    intensity: float
    brainwave_band: str
    fade_ms: int = 2000


@dataclass(frozen=True, slots=True)
class SongCue:
    """A song slot in the rhythm of the day (plan §6).

    A stub through M4 — the streaming pillar (M5) fills in real tracks. Until
    then a cue is a placeholder the director schedules and the player shows as
    "song slot", so the felt cadence and running order are correct now and the
    contract is stable for when audio arrives.
    """

    title: str = "—"
    artist: str = "—"
    source: str | None = None  # e.g. "spotify" once M5 lands
    uri: str | None = None  # provider track URI, when available
    duration_s: float = 180.0


@dataclass(frozen=True, slots=True)
class Segment:
    """One timed entry in a broadcast plan.

    ``title`` names the segment's topic/source ("Hacker News front page",
    "Repository activity") so a multi-source rundown reads as distinct segments.
    """

    kind: str  # "news" | "music" | "song"
    start_s: float
    duration_s: float
    audio: AudioRef | None = None
    script: Script | None = None
    title: str | None = None


@dataclass(frozen=True, slots=True)
class BroadcastPlan:
    """An ordered, timed list of segments for a tenant over a window."""

    segments: list[Segment] = field(default_factory=list)
    tenant: str | None = None
    window_start: datetime | None = None
    window_end: datetime | None = None
