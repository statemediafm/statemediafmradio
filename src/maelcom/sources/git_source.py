"""Git source: recent commits of a repository → NewsItems.

Accepts either a local path or a remote URL. Remote repos are shallow-cloned
(bare, ``--depth`` = ``max_count``) into a temporary directory that is removed
after the poll. Uses ``git`` via subprocess (no libgit dependency). This is the
M1 demo source — point Maelcom at an active repo and it produces the activity
stream the newsroom summarizes.
"""

from __future__ import annotations

import re
import subprocess
import tempfile
from datetime import datetime

from ..core.models import NewsItem
from .base import Source, register_source

# ASCII unit/record separators keep parsing robust against newlines in bodies.
_FS = "\x1f"  # between fields
_RS = "\x1e"  # between commits
_FORMAT = _FS.join(["%H", "%an", "%aI", "%s", "%b"]) + _RS

# scp-like remote form, e.g. git@gitlab.com:meltano/meltano.git
_SCP_LIKE = re.compile(r"^[^/@]+@[^/:]+:")


def is_remote(repo: str) -> bool:
    """True if ``repo`` looks like a URL/scp remote rather than a local path."""
    return "://" in repo or bool(_SCP_LIKE.match(repo))


@register_source
class GitSource(Source):
    """Read recent commits from a local git directory or a remote URL."""

    name = "git"

    def __init__(self, repo: str, max_count: int = 50) -> None:
        self.repo = repo
        self.max_count = max_count
        self.project = _repo_name(repo)

    def poll(self, since: datetime | None = None) -> list[NewsItem]:
        if is_remote(self.repo):
            with tempfile.TemporaryDirectory(prefix="maelcom-git-") as tmp:
                self._clone(self.repo, tmp)
                return self._log(tmp, since)
        return self._log(self.repo, since)

    def _clone(self, url: str, dest: str) -> None:
        subprocess.run(
            ["git", "clone", "--bare", "--quiet", f"--depth={self.max_count}", url, dest],
            check=True,
            capture_output=True,
            text=True,
        )

    def _log(self, path: str, since: datetime | None) -> list[NewsItem]:
        cmd = [
            "git",
            "-C",
            path,
            "log",
            f"--max-count={self.max_count}",
            f"--pretty=format:{_FORMAT}",
        ]
        if since is not None:
            cmd.append(f"--since={since.isoformat()}")
        out = subprocess.run(cmd, capture_output=True, text=True, check=True).stdout
        return [self._to_item(rec) for rec in out.split(_RS) if rec.strip()]

    def _to_item(self, record: str) -> NewsItem:
        sha, author, iso, subject, body = (record.strip("\n").split(_FS) + ["", "", "", "", ""])[:5]
        return NewsItem(
            id=sha,
            source=self.name,
            kind="commit",
            title=subject,
            body=body.strip(),
            origin=self.project,
            actors=[author] if author else [],
            timestamp=_parse_iso(iso),
            refs=[sha],
            raw={"repo": self.repo},
        )


def _repo_name(repo: str) -> str:
    """The project name from a repo path or URL, for on-air attribution.

    e.g. ``/home/me/RFClassifier`` → ``RFClassifier``;
    ``https://gitlab.com/meltano/meltano.git`` → ``meltano``.
    """
    name = repo.rstrip("/").split("/")[-1]
    return name.removesuffix(".git")


def _parse_iso(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None
