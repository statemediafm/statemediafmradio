"""Director: the rhythm of the day — a program clock over the station.

Turns the station cadences into a **running order** of foreground cues — news
bulletins on a 17-minute cadence, song slots between them (stubbed until M5) —
and keeps a *felt cadence*: it never lets the foreground fall quiet for longer
than a few minutes, dropping in brief station idents to bridge the gaps, with
the generative music playing underneath throughout.

Pure and deterministic — like :mod:`maelcom.core.schedule`, it takes explicit
times and never reads the wall clock, so the whole running order is unit-testable.
``serve`` supplies the session clock and airs the cues; the browser renders the
order. See PLAN.md §5.5 / M4.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .models import SongCue
from .schedule import Cadence

# Station cadences and felt-cadence bounds (seconds).
NEWS_EVERY_S = 17 * 60.0  # a news bulletin every 17 minutes (M4)
SONG_EVERY_S = 20 * 60.0  # a song slot every 20 minutes...
SONG_OFFSET_S = 10 * 60.0  # ...offset so it lands between the bulletins
FELT_MAX_GAP_S = 5 * 60.0  # never leave the foreground quiet longer than this
FELT_MIN_GAP_S = 2 * 60.0  # ...and don't drop an ident right before a real cue


@dataclass(frozen=True, slots=True)
class Cue:
    """One foreground moment in the running order."""

    kind: str  # "news" | "song" | "ident"
    at_s: float
    topic: str | None = None  # the news bulletin's topic label, if any
    cue: SongCue | None = None  # the song slot


def _slots_after(cadence: Cadence, lo: float, hi: float) -> list[float]:
    """Airing times ``t`` in the half-open ``(lo, hi]`` — what fell due since the
    last poll (``schedule.Cadence.slots_in`` is the ``[start, end)`` complement)."""
    k = math.floor((lo - cadence.offset_s) / cadence.every_s) + 1
    t = cadence.offset_s + k * cadence.every_s
    out: list[float] = []
    while t <= hi:
        if t > lo:
            out.append(t)
        t += cadence.every_s
    return out


class Director:
    """Schedules the station's foreground cues over a session-relative clock.

    ``news`` / ``song`` are :class:`~maelcom.core.schedule.Cadence` lattices;
    ``felt_max_gap_s`` is the longest the foreground may stay quiet before an
    ident is inserted. Topic-agnostic: a news *bulletin* covers whatever activity
    is fresh when it airs — the caller (``serve``) supplies the content.
    """

    def __init__(
        self,
        *,
        news: Cadence | None = None,
        song: Cadence | None = None,
        felt_max_gap_s: float = FELT_MAX_GAP_S,
    ) -> None:
        self.news = news or Cadence(NEWS_EVERY_S)
        self.song = song or Cadence(SONG_EVERY_S, SONG_OFFSET_S)
        self.felt_max_gap_s = felt_max_gap_s

    def _foreground(self, start_s: float, end_s: float) -> list[Cue]:
        """News + song cues in ``[start_s, end_s)``, sorted (news wins ties)."""
        cues = [Cue("news", t) for t in self.news.slots_in(start_s, end_s)]
        cues += [Cue("song", t, cue=SongCue()) for t in self.song.slots_in(start_s, end_s)]
        cues.sort(key=lambda c: (c.at_s, 0 if c.kind == "news" else 1))
        return cues

    def running_order(self, window_s: float, *, start_s: float = 0.0) -> list[Cue]:
        """The full running order for ``[start_s, start_s + window_s)``: news and
        song cues with station idents dropped in so no gap exceeds
        ``felt_max_gap_s`` — the guaranteed 2–5 minute felt cadence."""
        end_s = start_s + window_s
        order: list[Cue] = []
        last = start_s
        for cue in self._foreground(start_s, end_s):
            order.extend(self._idents(last, cue.at_s))
            order.append(cue)
            last = cue.at_s
        order.extend(self._idents(last, end_s))  # bridge the tail to the window end
        return order

    def _idents(self, last_s: float, next_s: float) -> list[Cue]:
        """Ident cues **evenly** filling ``(last_s, next_s)`` when the gap exceeds
        ``felt_max_gap_s``. Even spacing (rather than fixed strides from the last
        cue) guarantees every sub-gap is ``≤ felt_max_gap_s`` with no crowded
        straggler before the next cue — the 2–5 minute felt-cadence guarantee."""
        gap = next_s - last_s
        if gap <= self.felt_max_gap_s:
            return []
        n = math.ceil(gap / self.felt_max_gap_s) - 1  # idents needed to break the gap
        step = gap / (n + 1)
        return [Cue("ident", last_s + step * (i + 1)) for i in range(n)]

    def next_cue(self, now_s: float) -> Cue | None:
        """The next news/song cue strictly after ``now_s`` (idents excluded)."""
        horizon = now_s + 2 * max(self.news.every_s, self.song.every_s)
        upcoming = [c for c in self._foreground(now_s, horizon) if c.at_s > now_s]
        return upcoming[0] if upcoming else None

    def due_cues(self, since_s: float, now_s: float) -> list[Cue]:
        """News/song cues that fell due in ``(since_s, now_s]`` — what ``serve``
        should air this tick. News wins ties."""
        cues = [Cue("news", t) for t in _slots_after(self.news, since_s, now_s)]
        cues += [Cue("song", t, cue=SongCue()) for t in _slots_after(self.song, since_s, now_s)]
        cues.sort(key=lambda c: (c.at_s, 0 if c.kind == "news" else 1))
        return cues

    def news_due(self, since_s: float, now_s: float) -> bool:
        """Whether a news bulletin's slot fell due in ``(since_s, now_s]``."""
        return bool(_slots_after(self.news, since_s, now_s))
