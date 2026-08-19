"""Source plugin interface + a minimal in-process registry.

A ``Source`` connects to one platform (git, Slack, Jira, Grafana, ...), pulls
recent activity, and normalizes it to ``NewsItem``s. The registry is a stand-in
for entry-point discovery (plan §5.1) — enough for M1, swappable later without
touching call sites.
"""

from __future__ import annotations

import copy
from abc import ABC, abstractmethod
from datetime import datetime

from ..core.models import NewsItem


class Source(ABC):
    """A pollable activity source. Implementations set a unique ``name``."""

    name: str

    # Instance attributes that ``poll`` *consumes* — e.g. a recency cursor that
    # advances so the next poll only sees newer items. ``probe`` snapshots and
    # restores these so a non-destructive "Test" can't eat the recency window.
    # Stateless sources leave it empty (probe == poll).
    _PROBE_STATE: tuple[str, ...] = ()
    # A subset of ``_PROBE_STATE``: the recency cursor(s) to RESET (to ``None``,
    # their first-poll value) for a full-window probe, so the source returns its
    # whole window (``max_age``) rather than just the delta since the last poll.
    _WINDOW_RESET: tuple[str, ...] = ()

    @abstractmethod
    def poll(self, since: datetime | None = None) -> list[NewsItem]:
        """Return recent NewsItems, optionally only those newer than ``since``."""
        raise NotImplementedError

    def probe(self, since: datetime | None = None, *, full_window: bool = False) -> list[NewsItem]:
        """Poll **without consuming** the recency window. Snapshots/restores
        ``_PROBE_STATE`` around the poll so a subsequent real poll still returns the
        same items (a Test / an on-demand read must not make the broadcast miss them).

        With ``full_window`` the recency cursor(s) in ``_WINDOW_RESET`` are reset
        first, so the source yields its **whole window** (``max_age``, e.g. the last
        12 h) rather than only what changed since the last poll — used by "Newscast
        now" to present each source's last full window."""
        saved = {k: copy.copy(getattr(self, k)) for k in self._PROBE_STATE if hasattr(self, k)}
        try:
            if full_window:
                for k in self._WINDOW_RESET:
                    if hasattr(self, k):
                        setattr(self, k, None)
            return self.poll(since)
        finally:
            for k, v in saved.items():
                setattr(self, k, v)


_REGISTRY: dict[str, type[Source]] = {}


def register_source(cls: type[Source]) -> type[Source]:
    """Class decorator: register a Source under its ``name``."""
    _REGISTRY[cls.name] = cls
    return cls


def get_source(name: str) -> type[Source]:
    if name not in _REGISTRY:
        raise KeyError(f"No source registered as {name!r} (have: {sorted(_REGISTRY)})")
    return _REGISTRY[name]
