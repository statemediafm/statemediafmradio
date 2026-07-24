"""Rhythm-of-the-day scheduling: air sources as timed segments (plan §5.5).

Each **programme** is a recurring news segment about one topic (a source), aired
on its own **cadence** (every *N* seconds, shifted by an offset). Giving each
source a different cadence/offset makes them air at different times, so a
multi-source broadcast reads as a rundown of distinct segments rather than one
blob.

Pure and deterministic — scheduling takes an explicit window and never reads the
wall clock, so it is fully testable. The CLI supplies "now" and maps the
relative slot times to clock times for display.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .models import AudioRef, BroadcastPlan, Script, Segment


@dataclass(frozen=True, slots=True)
class Cadence:
    """Airs on the lattice ``offset_s + k*every_s`` for integer ``k``."""

    every_s: float
    offset_s: float = 0.0

    def slots_in(self, start_s: float, end_s: float) -> list[float]:
        """Every airing time in the half-open window ``[start_s, end_s)``."""
        if self.every_s <= 0:
            raise ValueError("Cadence.every_s must be positive")
        k = math.ceil((start_s - self.offset_s) / self.every_s)
        slots: list[float] = []
        t = self.offset_s + k * self.every_s
        while t < end_s:
            if t >= start_s:
                slots.append(t)
            t += self.every_s
        return slots


@dataclass(frozen=True, slots=True)
class Programme:
    """A recurring segment about one ``topic``, aired on its ``cadence``."""

    topic: str
    cadence: Cadence


def build_rundown(
    programmes: list[Programme], window_s: float, *, start_s: float = 0.0
) -> list[tuple[float, str]]:
    """Ordered ``(air_time_s, topic)`` for every airing in the window.

    Ties (two programmes scheduled at the same instant) break by topic name for
    a stable order.
    """
    slots: list[tuple[float, str]] = []
    for programme in programmes:
        for t in programme.cadence.slots_in(start_s, start_s + window_s):
            slots.append((t, programme.topic))
    slots.sort(key=lambda item: (item[0], item[1]))
    return slots


def assemble_broadcast(
    programmes: list[Programme],
    content: dict[str, tuple[Script, AudioRef]],
    window_s: float,
    *,
    start_s: float = 0.0,
    tenant: str | None = None,
) -> BroadcastPlan:
    """Place each programme's airings at their scheduled times as Segments.

    ``content`` maps a topic to its voiced ``(Script, AudioRef)``; a programme
    with no content is skipped (e.g. a source that returned nothing this cycle).
    """
    segments: list[Segment] = []
    for air_s, topic in build_rundown(programmes, window_s, start_s=start_s):
        pair = content.get(topic)
        if pair is None:
            continue
        script, audio = pair
        segments.append(
            Segment(
                kind="news",
                start_s=air_s,
                duration_s=audio.duration_ms / 1000.0,
                audio=audio,
                script=script,
                title=topic,
            )
        )
    return BroadcastPlan(segments=segments, tenant=tenant)
