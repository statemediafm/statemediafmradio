"""Shared helpers for reasoning about actors (people vs automation).

``is_bot`` is used by more than one pillar — the newsroom keeps bots out of the
spoken credit line and headlines, and generative music keeps them out of the
participant count — so it lives in ``core`` rather than either pillar.
"""

from __future__ import annotations

# Well-known automation accounts that don't carry a ``[bot]``/``-bot`` marker.
_BOT_NAMES = frozenset(
    {
        "dependabot",
        "renovate",
        "renovate-bot",
        "mergify",
        "codecov",
        "codspeed-hq",
        "sourcery-ai",
        "pre-commit-ci",
        "github-actions",
        "greenkeeper",
        "snyk-bot",
        "imgbot",
        "allcontributors",
        "stale",
        "semantic-release-bot",
    }
)


def is_bot(name: str) -> bool:
    """True if ``name`` looks like an automation/bot account, not a person.

    Catches the ``[bot]`` suffix GitHub/GitLab apps use (``dependabot[bot]``,
    ``codecov[bot]``), ``-bot``/``_bot`` suffixes, and a few well-known bots that
    use neither.
    """
    n = name.strip().lower()
    return n.endswith(("[bot]", "-bot", "_bot")) or n in _BOT_NAMES
