"""Source plugin interface + a minimal in-process registry.

A ``Source`` connects to one platform (git, Slack, Jira, Grafana, ...), pulls
recent activity, and normalizes it to ``NewsItem``s. The registry is a stand-in
for entry-point discovery (plan §5.1) — enough for M1, swappable later without
touching call sites.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime

from ..core.models import NewsItem


class Source(ABC):
    """A pollable activity source. Implementations set a unique ``name``."""

    name: str

    @abstractmethod
    def poll(self, since: datetime | None = None) -> list[NewsItem]:
        """Return recent NewsItems, optionally only those newer than ``since``."""
        raise NotImplementedError


_REGISTRY: dict[str, type[Source]] = {}


def register_source(cls: type[Source]) -> type[Source]:
    """Class decorator: register a Source under its ``name``."""
    _REGISTRY[cls.name] = cls
    return cls


def get_source(name: str) -> type[Source]:
    if name not in _REGISTRY:
        raise KeyError(f"No source registered as {name!r} (have: {sorted(_REGISTRY)})")
    return _REGISTRY[name]
