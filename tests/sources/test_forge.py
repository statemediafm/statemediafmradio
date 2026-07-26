"""Tests for the forge source (issues + MR/PR comments), fully offline.

A fake HTTP getter returns canned GitHub/GitLab payloads keyed by URL, so the
source is exercised deterministically with no network.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from maelcom.sources import detect_forge, open_source
from maelcom.sources.forge import ForgeSource
from maelcom.sources.git_source import GitSource


def test_detect_forge_recognizes_hosts_and_slugs():
    assert detect_forge("https://github.com/meltano/meltano") == ("github", "meltano/meltano")
    assert detect_forge("https://gitlab.com/meltano/meltano.git") == ("gitlab", "meltano/meltano")
    assert detect_forge("git@github.com:acme/widgets.git") == ("github", "acme/widgets")
    # Local paths and unknown hosts are not forges.
    assert detect_forge("/home/me/repo") is None
    assert detect_forge("https://example.com/x/y") is None


def test_detect_forge_normalizes_work_item_urls():
    # A pasted issue / PR / MR URL resolves to its project root.
    assert detect_forge("https://github.com/owner/repo/issues/123") == ("github", "owner/repo")
    assert detect_forge("https://github.com/owner/repo/pull/5") == ("github", "owner/repo")
    assert detect_forge("https://gitlab.com/group/project/-/merge_requests/3") == (
        "gitlab", "group/project")
    # GitLab nested groups keep their full project path.
    assert detect_forge("https://gitlab.com/group/sub/project/-/issues/1") == (
        "gitlab", "group/sub/project")


def test_forge_max_age_keeps_only_recent_updates():
    now = datetime(2026, 7, 25, 12, 0, 0, tzinfo=UTC)

    def iso(days):
        return (now - timedelta(days=days)).isoformat()

    issues = [
        {"number": 1, "title": "fresh", "user": {"login": "a"}, "comments": 0,
         "updated_at": iso(1), "state": "open", "html_url": "u1"},
        {"number": 2, "title": "stale", "user": {"login": "b"}, "comments": 0,
         "updated_at": iso(30), "state": "open", "html_url": "u2"},
    ]
    src = ForgeSource("https://github.com/o/r", get=lambda url: issues,
                      max_age=7 * 86400, now=lambda: now)
    assert [n.title for n in src.poll()] == ["fresh"]  # 30-day-old item filtered out

    # max_age=None → the cap is removed and nothing is filtered on the first poll.
    src2 = ForgeSource("https://github.com/o/r", get=lambda url: issues,
                       max_age=None, now=lambda: now)
    assert {n.title for n in src2.poll()} == {"fresh", "stale"}


def test_forge_default_window_is_12h():
    now = datetime(2026, 7, 25, 12, 0, 0, tzinfo=UTC)
    issues = [
        {"number": 1, "title": "recent", "user": {"login": "a"}, "comments": 0,
         "updated_at": (now - timedelta(hours=6)).isoformat(), "state": "open", "html_url": "u1"},
        {"number": 2, "title": "yesterday", "user": {"login": "b"}, "comments": 0,
         "updated_at": (now - timedelta(hours=20)).isoformat(), "state": "open", "html_url": "u2"},
    ]
    # No max_age given → the 12h radio-recent default; 20h-old item is dropped.
    src = ForgeSource("https://github.com/o/r", get=lambda url: issues, now=lambda: now)
    assert [n.title for n in src.poll()] == ["recent"]


def test_forge_returns_only_updates_since_last_poll():
    t0 = datetime(2026, 7, 25, 12, 0, 0, tzinfo=UTC)
    state = {"now": t0, "issues": [
        {"number": 1, "title": "A", "user": {"login": "a"}, "comments": 0,
         "updated_at": (t0 - timedelta(hours=2)).isoformat(), "state": "open", "html_url": "u1"},
    ]}
    src = ForgeSource("https://github.com/o/r",
                      get=lambda url: state["issues"], now=lambda: state["now"])
    assert [n.title for n in src.poll()] == ["A"]  # first poll: within the 12h window

    # An hour later A is unchanged (last touched 3h ago) and B was just updated.
    state["now"] = t0 + timedelta(hours=1)
    state["issues"] = state["issues"] + [
        {"number": 2, "title": "B", "user": {"login": "b"}, "comments": 0,
         "updated_at": (state["now"] - timedelta(minutes=30)).isoformat(),
         "state": "open", "html_url": "u2"},
    ]
    assert [n.title for n in src.poll()] == ["B"]  # only the delta since the last poll


def test_open_source_routes_forge_vs_git():
    assert isinstance(open_source("https://github.com/a/b"), ForgeSource)
    assert isinstance(open_source("/local/path"), GitSource)


class _FakeGet:
    """Serve canned JSON by URL substring, recording every URL requested."""

    def __init__(self, routes: dict[str, object]):
        self.routes = routes
        self.calls: list[str] = []

    def __call__(self, url: str):
        self.calls.append(url)
        for needle, payload in self.routes.items():
            if needle in url:
                return payload
        raise AssertionError(f"unexpected URL: {url}")


def test_github_issues_and_prs_with_latest_comment():
    routes = {
        "/issues?": [
            {
                "number": 101,
                "title": "Scheduler hangs",
                "state": "open",
                "updated_at": "2026-07-24T10:00:00Z",
                "comments": 2,
                "comments_url": "https://api.github.com/repos/acme/widgets/issues/101/comments",
                "user": {"login": "alice"},
                "body": "original description",
                # no "pull_request" key → it's an issue
            },
            {
                "number": 102,
                "title": "Fix the hang",
                "state": "open",
                "updated_at": "2026-07-24T09:00:00Z",
                "comments": 0,
                "comments_url": "https://api.github.com/repos/acme/widgets/issues/102/comments",
                "user": {"login": "bob"},
                "body": "PR body",
                "pull_request": {"url": "..."},
            },
        ],
        "/issues/101/comments": [
            {"user": {"login": "carol"}, "body": "latest word on the hang"}
        ],
    }
    fake = _FakeGet(routes)
    # max_age=None here: this exercises comment attachment, not recency filtering.
    items = ForgeSource("https://github.com/acme/widgets", max_count=10, get=fake,
                        max_age=None).poll()

    assert [i.kind for i in items] == ["issue", "pull_request"]
    issue, pr = items
    # The issue's body is its *latest comment*, and the commenter is an actor.
    assert issue.body == "latest word on the hang"
    assert issue.actors == ["alice", "carol"]
    assert issue.id == "github:issue:101"
    assert issue.origin == "widgets"  # attributed to the project name
    # The PR has no comments → no comment call was made, body falls back.
    assert pr.body == "PR body"
    assert pr.actors == ["bob"]
    assert not any("/issues/102/comments" in c for c in fake.calls)


def test_github_degrades_when_comments_forbidden():
    def boom_on_comments(url: str):
        if "/comments" in url:
            raise OSError("403 rate limited")
        return [
            {
                "number": 5,
                "title": "Thing",
                "state": "open",
                "updated_at": "2026-07-24T10:00:00Z",
                "comments": 3,
                "comments_url": "https://api.github.com/repos/a/b/issues/5/comments",
                "user": {"login": "dev"},
                "body": "desc",
            }
        ]

    items = ForgeSource("https://github.com/a/b", get=boom_on_comments, max_age=None).poll()
    # Comment fetch failed → falls back to the issue's own description, no crash.
    assert items[0].body == "desc"
    assert items[0].actors == ["dev"]


def test_forge_rejects_non_forge_url():
    with pytest.raises(ValueError, match="not a recognized"):
        ForgeSource("/local/path")
