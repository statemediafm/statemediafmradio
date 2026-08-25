"""Sources pillar: connect to platforms and normalize activity to NewsItems."""

from __future__ import annotations

from .base import Source, get_source, register_source
from .forge import DEFAULT_MAX_AGE as FORGE_DEFAULT_MAX_AGE
from .forge import ForgeSource, detect_forge
from .git_source import GitSource, is_remote
from .hackernews import HackerNewsSource
from .jira import JiraSource
from .pagerduty import PagerDutySource
from .slack import SlackSource


def open_source(
    repo: str,
    max_count: int = 20,
    token: str | None = None,
    max_age: float | None = FORGE_DEFAULT_MAX_AGE,
    gitlab_base: str | None = None,
    github_base: str | None = None,
) -> Source:
    """Pick the right source for ``repo``.

    A GitHub/GitLab URL → ``ForgeSource`` (issues + merge/pull requests with
    their latest comments). Anything else (a local path, a bare remote) →
    ``GitSource`` (recent commits), which is all that is available without a
    forge API. ``max_age`` (seconds) caps a forge to items opened within the
    window (default 60d — see ``ForgeSource``); ``None`` removes the cap.
    ``gitlab_base`` / ``github_base`` name a **self-hosted GitLab** or **GitHub
    Enterprise** instance so its URLs are recognized + polled via its API (from
    the Settings ``[gitlab] / [github] endpoint`` config).
    """
    if detect_forge(repo, gitlab_base=gitlab_base, github_base=github_base) is not None:
        return ForgeSource(
            repo, max_count=max_count, token=token, max_age=max_age,
            gitlab_base=gitlab_base, github_base=github_base,
        )
    return GitSource(repo, max_count=max_count)


__all__ = [
    "FORGE_DEFAULT_MAX_AGE",
    "ForgeSource",
    "GitSource",
    "HackerNewsSource",
    "JiraSource",
    "PagerDutySource",
    "SlackSource",
    "Source",
    "detect_forge",
    "get_source",
    "is_remote",
    "open_source",
    "register_source",
]
