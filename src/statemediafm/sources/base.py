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

    @abstractmethod
    def poll(self, since: datetime | None = None) -> list[NewsItem]:
        """Return recent NewsItems, optionally only those newer than ``since``."""
        raise NotImplementedError

    def probe(self, since: datetime | None = None) -> list[NewsItem]:
        """Poll **without consuming** the recency window — for the Settings "Test"
        button. Snapshots/restores ``_PROBE_STATE`` around the poll so a subsequent
        real poll still returns the same items (a Test must not make the broadcast
        miss them)."""
        saved = {k: copy.copy(getattr(self, k)) for k in self._PROBE_STATE if hasattr(self, k)}
        try:
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
