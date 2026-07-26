"""PagerDuty source: recent incidents → NewsItems.

Reads recent incidents from the PagerDuty REST API and normalizes each into a
``NewsItem``. The API token (and optional base URL) come from the gitignored auth
config (Settings tab → "pagerduty"); PagerDuty uses ``Authorization: Token
token=…``. stdlib-only; the HTTP getter is injectable for offline tests.
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterable
from datetime import datetime
from typing import Any

from ..auth import source_endpoint, source_token
from ..core.models import NewsItem
from .base import Source, register_source

_API = "https://api.pagerduty.com"


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return None


@register_source
class PagerDutySource(Source):
    """Summarize recent PagerDuty incidents."""

    name = "pagerduty"

    def __init__(
        self,
        statuses: Iterable[str] = ("triggered", "acknowledged"),
        token: str | None = None,
        endpoint: str | None = None,
        max_count: int = 25,
        get: Callable[[str], Any] | None = None,
    ) -> None:
        self.statuses = tuple(statuses)
        self.token = token or source_token("pagerduty")
        self.base = (endpoint or source_endpoint("pagerduty") or _API).rstrip("/")
        self.max_count = max_count
        self._get = get or self._http_json

    def _http_json(self, url: str) -> Any:
        headers = {
            "User-Agent": "maelcom",
            "Accept": "application/vnd.pagerduty+json;version=2",
        }
        if self.token:
            headers["Authorization"] = f"Token token={self.token}"
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.load(resp)

    def poll(self, since: datetime | None = None) -> list[NewsItem]:
        if not self.token:
            return []
        params = [("sort_by", "created_at:desc"), ("limit", str(self.max_count))]
        params += [("statuses[]", s) for s in self.statuses]
        data = self._get(f"{self.base}/incidents?{urllib.parse.urlencode(params)}") or {}
        items: list[NewsItem] = []
        for inc in data.get("incidents", []):
            title = (inc.get("title") or inc.get("summary") or "").strip()
            if not title:
                continue
            status = inc.get("status", "")
            urgency = inc.get("urgency", "")
            service = (inc.get("service") or {}).get("summary", "")
            assignee = next(
                (
                    (a.get("assignee") or {}).get("summary")
                    for a in inc.get("assignments", [])
                    if (a.get("assignee") or {}).get("summary")
                ),
                None,
            )
            bits = [b for b in (f"{urgency} urgency" if urgency else "", status,
                                f"on {service}" if service else "") if b]
            items.append(
                NewsItem(
                    id=f"pd:{inc.get('id', '')}",
                    source=self.name,
                    kind="incident",
                    title=title,
                    body=(", ".join(bits) + ".") if bits else "",
                    origin="PagerDuty",
                    actors=[assignee] if assignee else [],
                    timestamp=_parse_dt(inc.get("created_at")),
                    raw={"status": status, "urgency": urgency, "service": service},
                )
            )
        return items
