"""Hacker News source: the news.ycombinator.com front page → NewsItems.

Reads the current top ("front page") stories via the official Hacker News
Firebase API (the same data that renders news.ycombinator.com). Each story
becomes a ``NewsItem`` whose body notes its score, comment count, and source
domain — enough for the newsroom to voice a front-page rundown.

stdlib-only (``urllib`` + ``json``) so it runs inside the zipapp; the HTTP
getter is injectable for offline, deterministic tests.
"""

from __future__ import annotations

import json
import urllib.request
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

from ..core.models import NewsItem
from .base import Source, register_source

_API = "https://hacker-news.firebaseio.com/v0"


@register_source
class HackerNewsSource(Source):
    """Summarize the Hacker News front page (top stories)."""

    name = "hackernews"

    def __init__(self, max_count: int = 10, get: Callable[[str], Any] | None = None) -> None:
        self.max_count = max_count
        self._get = get or self._http_json

    def _http_json(self, url: str) -> Any:
        req = urllib.request.Request(url, headers={"User-Agent": "statemediafm"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.load(resp)

    def poll(self, since: datetime | None = None) -> list[NewsItem]:
        ids = self._get(f"{_API}/topstories.json") or []
        items: list[NewsItem] = []
        for rank, story_id in enumerate(ids[: self.max_count], start=1):
            story = self._get(f"{_API}/item/{story_id}.json")
            if not story or not story.get("title"):
                continue
            url = story.get("url") or f"https://news.ycombinator.com/item?id={story_id}"
            domain = urlparse(url).netloc.removeprefix("www.") or "news.ycombinator.com"
            score = story.get("score", 0)
            n_comments = story.get("descendants", 0)
            ts = story.get("time")
            items.append(
                NewsItem(
                    id=f"hn:{story_id}",
                    source=self.name,
                    kind="story",
                    title=story["title"],
                    body=f"{score} points and {n_comments} comments, via {domain}.",
                    origin="Hacker News",
                    actors=[story["by"]] if story.get("by") else [],
                    timestamp=datetime.fromtimestamp(ts, tz=UTC) if ts else None,
                    refs=[url],
                    raw={"rank": rank, "score": score, "comments": n_comments, "domain": domain},
                )
            )
        return items
