"""Jira source: a project's recently-updated issues → NewsItems.

Reads recent issues from a Jira project via the Jira Cloud REST API and
normalizes each into a ``NewsItem``. The site URL (``endpoint``) and token come
from the gitignored auth config (Settings tab → "jira"); Jira Cloud uses Basic
auth, so the token is your ``email:api_token`` pair (entered whole in the token
field). stdlib-only; the HTTP getter is injectable for offline tests.
"""

from __future__ import annotations

import base64
import json
import urllib.parse
import urllib.request
from collections.abc import Callable
from datetime import datetime
from typing import Any

from ..auth import source_endpoint, source_token
from ..core.models import NewsItem
from .base import Source, register_source


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return None


@register_source
class JiraSource(Source):
    """Summarize a Jira project's recently-updated issues."""

    name = "jira"

    def __init__(
        self,
        project: str,
        token: str | None = None,
        endpoint: str | None = None,
        max_count: int = 25,
        get: Callable[[str], Any] | None = None,
    ) -> None:
        self.project = project
        self.token = token or source_token("jira")  # "email:api_token"
        self.base = (endpoint or source_endpoint("jira") or "").rstrip("/")
        self.max_count = max_count
        self._get = get or self._http_json

    def _http_json(self, url: str) -> Any:
        headers = {"User-Agent": "statemediafm", "Accept": "application/json"}
        if self.token:
            headers["Authorization"] = "Basic " + base64.b64encode(self.token.encode()).decode()
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.load(resp)

    def poll(self, since: datetime | None = None) -> list[NewsItem]:
        if not (self.token and self.base):
            return []
        query = urllib.parse.urlencode(
            {
                "jql": f"project = {self.project} ORDER BY updated DESC",
                "maxResults": self.max_count,
                "fields": "summary,status,assignee,updated,issuetype",
            }
        )
        data = self._get(f"{self.base}/rest/api/3/search?{query}") or {}
        items: list[NewsItem] = []
        for issue in data.get("issues", []):
            fields = issue.get("fields") or {}
            summary = (fields.get("summary") or "").strip()
            if not summary:
                continue
            key = issue.get("key", "")
            status = (fields.get("status") or {}).get("name", "")
            itype = (fields.get("issuetype") or {}).get("name", "issue")
            assignee = (fields.get("assignee") or {}).get("displayName")
            body = f"{itype} {key}" + (f", {status}" if status else "") + "."
            items.append(
                NewsItem(
                    id=f"jira:{key}",
                    source=self.name,
                    kind="issue",
                    title=summary,
                    body=body,
                    origin=f"Jira {self.project}",
                    actors=[assignee] if assignee else [],
                    timestamp=_parse_dt(fields.get("updated")),
                    raw={"key": key, "status": status, "type": itype},
                )
            )
        return items
