"""Sources pillar: connect to platforms and normalize activity to NewsItems."""

from __future__ import annotations

from .base import Source, get_source, register_source
from .forge import ForgeSource, detect_forge
from .git_source import GitSource, is_remote
from .hackernews import HackerNewsSource
from .slack import SlackSource


def open_source(repo: str, max_count: int = 20, token: str | None = None) -> Source:
    """Pick the right source for ``repo``.

    A GitHub/GitLab URL → ``ForgeSource`` (issues + merge/pull requests with
    their latest comments). Anything else (a local path, a bare remote) →
    ``GitSource`` (recent commits), which is all that is available without a
    forge API.
    """
    if detect_forge(repo) is not None:
        return ForgeSource(repo, max_count=max_count, token=token)
    return GitSource(repo, max_count=max_count)


__all__ = [
    "ForgeSource",
    "GitSource",
    "HackerNewsSource",
    "SlackSource",
    "Source",
    "detect_forge",
    "get_source",
    "is_remote",
    "open_source",
    "register_source",
]
