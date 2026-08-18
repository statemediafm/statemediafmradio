"""Forge source: issues / work items and merge-request activity → NewsItems.

Unlike commits (which live in git itself), issues, work items, and MR/PR
comments live in the hosting platform's API. This source reads a GitHub or
GitLab **project's** — or a GitLab **group's** (``…/groups/team``, aggregating
its projects) — most-recently-updated issues and merge/pull requests, attaches
the *latest comment* on each, and normalizes them to ``NewsItem``s.

Design notes:
- **stdlib only** (``urllib`` + ``json``), so it still runs inside the
  zero-dependency zipapp.
- The HTTP getter is injectable (``get=``), so tests drive it fully offline
  with canned API payloads — no network, deterministic.
- Auth is optional: public projects work unauthenticated (subject to the
  platform's rate limits). A token (``--token`` / ``GITHUB_TOKEN`` /
  ``GITLAB_TOKEN``) raises limits and reaches private items.
"""

from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from ..core.models import NewsItem
from .base import Source, register_source

# Recognized public forge hosts → platform key. A **self-hosted GitLab** instance
# is recognized too, by passing its host via ``gitlab_base`` (from the Settings
# ``[gitlab] endpoint`` config) — see ``detect_forge``.
_HOSTS = {"github.com": "github", "gitlab.com": "gitlab"}

# Default GitLab API base when no self-hosted instance is configured.
GITLAB_DEFAULT_BASE = "https://gitlab.com"

# Default recency window: a radio reports *recent* news, so a forge airs work
# items updated since the last poll, and — on the first poll or after a long gap
# — never reaches back further than this (12 hours), not yesterday's activity.
DEFAULT_MAX_AGE = 12 * 3600.0


def _host_of(url: str) -> str:
    """The bare hostname of a URL/repo — https, scp-like (``git@host:owner/repo``),
    or bare (``host/owner/repo``) — minus any scheme, credentials, or port. ``""``
    for a local path (no host)."""
    u = (url or "").strip()
    if not u:
        return ""
    if "://" not in u:
        u = "//" + u  # let urlsplit treat the leading token as a netloc
    netloc = urllib.parse.urlsplit(u).netloc
    return netloc.split("@")[-1].split(":")[0].lower()


def normalize_gitlab_base(url: str | None) -> str:
    """A GitLab API base URL: default to gitlab.com; add ``https://`` if the
    configured instance is given bare; drop any trailing slash."""
    u = (url or "").strip().rstrip("/")
    if not u:
        return GITLAB_DEFAULT_BASE
    return u if "://" in u else "https://" + u


def _parse_forge_url(
    repo: str, *, gitlab_base: str | None = None
) -> tuple[str, list[str], bool] | None:
    """Parse a forge URL into ``(platform, path_segments, is_group)``, or ``None``.

    Shared by :func:`detect_forge` and :class:`ForgeSource`. Recognizes github.com /
    gitlab.com — and, when ``gitlab_base`` names a **self-hosted GitLab** instance,
    that host too — in https or scp-like form, matching on the exact host (so
    ``gitlab.company.com`` is not mistaken for gitlab.com). A pasted **work-item
    URL** (an issue / PR / MR link) normalizes to its project/group root
    (``…/g/p/-/merge_requests/3`` → ``g/p``).

    The GitLab **``groups/`` marker** is consumed: ``gitlab.com/groups/team/sub``
    yields ``("gitlab", ["team", "sub"], True)`` — a group — while
    ``gitlab.com/team/project`` yields ``("gitlab", ["team", "project"], False)`` —
    a project. Either way the returned ``path_segments`` name the group or project.
    GitHub has no groups (``is_group`` is always ``False``; owner/repo only).
    """
    hosts = dict(_HOSTS)
    if gitlab_base:
        gh = _host_of(gitlab_base)
        if gh:
            hosts[gh] = "gitlab"
    host = _host_of(repo)
    platform = hosts.get(host)
    if platform is None:
        return None
    # The path after the host, for https or scp-like (git@host:owner/repo) forms.
    path = repo.split(host, 1)[1].lstrip(":/").split("#", 1)[0].split("?", 1)[0]
    path = path.split("/-/", 1)[0]  # work-item URLs: project/group sits before /-/
    path = path.removesuffix("/").removesuffix(".git")
    parts = [p for p in path.split("/") if p]
    # GitLab group URLs carry a leading ``groups/`` marker — consume it so the
    # remaining segments name the group (nested subgroups keep their full path).
    is_group = platform == "gitlab" and len(parts) >= 2 and parts[0] == "groups"
    if is_group:
        parts = parts[1:]
    elif platform == "github":
        # owner/repo are the first two segments (drop /issues/123, /pull/5, …).
        # GitLab projects keep the full (possibly nested) path — the API takes it whole.
        parts = parts[:2]
    # A group needs at least its own name; a project needs namespace/name.
    if len(parts) < (1 if is_group else 2):
        return None
    return platform, parts, is_group


def detect_forge(repo: str, *, gitlab_base: str | None = None) -> tuple[str, str] | None:
    """Return ``(platform, "owner/name")`` if ``repo`` is a known forge URL, else
    ``None`` (so callers fall back to the git source).

    A thin wrapper over :func:`_parse_forge_url` that joins the segments into a
    slug — the group or project path either way (the ``groups/`` marker is already
    consumed). Public signature is intentionally the ``(platform, slug)`` tuple.
    """
    parsed = _parse_forge_url(repo, gitlab_base=gitlab_base)
    if parsed is None:
        return None
    platform, parts, _is_group = parsed
    return platform, "/".join(parts)


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)  # 3.11+ handles the trailing "Z"
    except ValueError:
        return None


@register_source
class ForgeSource(Source):
    """Read issues + merge/pull requests (with latest comments) from a forge."""

    name = "forge"

    def __init__(
        self,
        repo: str,
        max_count: int = 20,
        token: str | None = None,
        get: Callable[[str], Any] | None = None,
        max_age: float | None = DEFAULT_MAX_AGE,
        now: Callable[[], datetime] | None = None,
        gitlab_base: str | None = None,
    ) -> None:
        parsed = _parse_forge_url(repo, gitlab_base=gitlab_base)
        if parsed is None:
            raise ValueError(f"{repo!r} is not a recognized GitHub/GitLab URL.")
        self.repo = repo
        self.platform, parts, self.is_group = parsed
        self.slug = "/".join(parts)
        # For GitLab, the API base — a self-hosted instance (``gitlab_base``) or
        # gitlab.com. GitHub always uses api.github.com.
        self.api_base = normalize_gitlab_base(gitlab_base) if self.platform == "gitlab" else GITLAB_DEFAULT_BASE
        self.project = parts[-1]  # group or project name, for on-air attribution
        self.max_count = max_count
        # The recency cap (seconds); None = no age limit. Combined with the last
        # poll time so each poll airs only what has changed since — but never
        # older than max_age. See ``_recent``.
        self.max_age = max_age
        self._now = now or (lambda: datetime.now(UTC))
        self._last_poll: datetime | None = None
        self.token = token or os.environ.get(
            "GITHUB_TOKEN" if self.platform == "github" else "GITLAB_TOKEN"
        )
        self._get = get or self._http_json
        # Once the platform rate-limits comment fetches, stop attempting more.
        self._comments_ok = True

    # --- HTTP -------------------------------------------------------------
    def _http_json(self, url: str) -> Any:
        headers = {"User-Agent": "statemediafm"}
        if self.platform == "github":
            headers["Accept"] = "application/vnd.github+json"
            if self.token:
                headers["Authorization"] = f"Bearer {self.token}"
        elif self.token:
            headers["PRIVATE-TOKEN"] = self.token
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.load(resp)

    def _latest_comment(self, url: str) -> Any | None:
        """Best-effort fetch of the single latest comment; degrade gracefully."""
        if not self._comments_ok:
            return None
        try:
            notes = self._get(url)
        except Exception:  # noqa: BLE001 — any fetch failure degrades to no comment
            # Rate-limited or forbidden (e.g. GitLab mirror) — stop trying.
            self._comments_ok = False
            return None
        return notes[-1] if notes else None

    # --- polling ----------------------------------------------------------
    def poll(self, since: datetime | None = None) -> list[NewsItem]:
        now = self._now()
        items = self._poll_github() if self.platform == "github" else self._poll_gitlab()
        items = self._recent(items, now, since)
        self._last_poll = now
        return items

    def _recent(
        self, items: list[NewsItem], now: datetime, since: datetime | None
    ) -> list[NewsItem]:
        """Keep only items updated since the effective cutoff — the more recent of
        the last poll (or an explicit ``since``) and ``now - max_age``. So a busy
        repo yields just what changed since last time, while the first poll (no
        prior time) is bounded to the ``max_age`` window. No cutoff → unchanged;
        items with an unknown update time are dropped once a cutoff applies."""
        cutoff: float | None = None
        if self.max_age is not None:
            cutoff = now.timestamp() - self.max_age
        prev = since or self._last_poll
        if prev is not None:
            cutoff = prev.timestamp() if cutoff is None else max(cutoff, prev.timestamp())
        if cutoff is None:
            return items
        return [n for n in items if n.timestamp is not None and n.timestamp.timestamp() >= cutoff]

    def _poll_github(self) -> list[NewsItem]:
        base = f"https://api.github.com/repos/{self.slug}"
        issues = self._get(
            f"{base}/issues?per_page={self.max_count}&sort=updated&direction=desc&state=all"
        )
        items: list[NewsItem] = []
        for it in issues:
            is_pr = "pull_request" in it
            comment = None
            if it.get("comments"):
                # Page straight to the last comment: per_page=1, page=<count>.
                comment = self._latest_comment(
                    f"{it['comments_url']}?per_page=1&page={it['comments']}"
                )
            actors = [it["user"]["login"]]
            body = it.get("body") or ""
            if comment:
                actors.append(comment["user"]["login"])
                body = comment["body"] or ""
            items.append(
                NewsItem(
                    id=f"github:{'pr' if is_pr else 'issue'}:{it['number']}",
                    source=self.name,
                    kind="pull_request" if is_pr else "issue",
                    title=it["title"],
                    body=body.strip()[:800],
                    origin=self.project,
                    actors=list(dict.fromkeys(actors)),
                    timestamp=_parse_ts(it.get("updated_at")),
                    refs=[it.get("html_url", "")],
                    raw={"platform": "github", "number": it["number"], "state": it["state"]},
                )
            )
        return items

    def _poll_gitlab(self) -> list[NewsItem]:
        # A group aggregates issues/MRs across its projects (``/groups/:id/…``); a
        # project reads its own (``/projects/:id/…``). ``self.slug`` names either.
        scope = "groups" if self.is_group else "projects"
        gid = urllib.parse.quote(self.slug, safe="")
        base = f"{self.api_base}/api/v4/{scope}/{gid}"
        api_v4 = f"{self.api_base}/api/v4"
        items: list[NewsItem] = []
        for kind, path in (("issue", "issues"), ("merge_request", "merge_requests")):
            listing = self._get(
                f"{base}/{path}?per_page={self.max_count}&order_by=updated_at&sort=desc"
            )
            for it in listing:
                comment = None
                if it.get("user_notes_count"):
                    # Notes live under the item's *project*, so a group listing (whose
                    # items span projects) fetches via each item's ``project_id``.
                    proj = it.get("project_id") if self.is_group else self.slug
                    proj_q = urllib.parse.quote(str(proj), safe="") if self.is_group else gid
                    comment = self._latest_comment(
                        f"{api_v4}/projects/{proj_q}/{path}/{it['iid']}/notes"
                        "?per_page=1&sort=desc&order_by=created_at"
                    )
                actors = [it["author"]["name"]]
                body = it.get("description") or ""
                if comment:
                    actors.append(comment["author"]["name"])
                    body = comment["body"] or ""
                items.append(
                    NewsItem(
                        # ``id`` is globally unique in GitLab (``iid`` is only unique
                        # per project, so it would collide across a group's projects).
                        id=f"gitlab:{kind}:{it.get('id', it['iid'])}",
                        source=self.name,
                        kind=kind,
                        title=it["title"],
                        body=body.strip()[:800],
                        origin=self.project,
                        actors=list(dict.fromkeys(actors)),
                        timestamp=_parse_ts(it.get("updated_at")),
                        refs=[it.get("web_url", "")],
                        raw={"platform": "gitlab", "iid": it["iid"], "state": it.get("state"),
                             "project_id": it.get("project_id")},
                    )
                )
        # Interleave by recency across issues and MRs, newest first. Sort by
        # epoch so tz-aware and tz-naive timestamps stay mutually comparable.
        items.sort(key=lambda n: n.timestamp.timestamp() if n.timestamp else 0.0, reverse=True)
        return items[: self.max_count]
