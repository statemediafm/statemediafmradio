"""Sources pillar: connect to platforms and normalize activity to NewsItems."""

from __future__ import annotations

from .base import Source, get_source, register_source
from .git_source import GitSource, is_remote

__all__ = ["GitSource", "Source", "get_source", "is_remote", "register_source"]
