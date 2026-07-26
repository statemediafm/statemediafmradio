"""Slack source: a channel's recent messages → NewsItems.

Reads recent messages from a Slack channel via the Slack Web API and normalizes
each into a ``NewsItem`` for the newsroom to summarize. Bot/system messages
(joins, integrations) are skipped; Slack markup (``<url|label>``, mentions) is
cleaned to plain text; user IDs are resolved to display names (cached).

The bot token (``xoxb-…``) and an optional base URL come from the gitignored auth
config (Settings tab → "slack"), or may be passed explicitly. stdlib-only
(``urllib`` + ``json``); the HTTP getter is injectable for offline tests.

Needs a bot token with ``channels:history`` / ``groups:history`` +
``channels:read`` + ``users:read`` scopes, and the bot in the channel.
"""

from __future__ import annotations

import html
import json
import re
import urllib.parse
import urllib.request
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from ..auth import source_endpoint, source_token
from ..core.models import NewsItem
from .base import Source, register_source

_API = "https://slack.com/api"
_CHANNEL_ID = re.compile(r"[CGD][A-Z0-9]{6,}$")  # a channel/group/DM ID, not a name


def _clean(text: str) -> str:
    """Slack markup → plain text: ``<url|label>``/``<#C|name>`` → label, ``<url>``
    → url, bare mentions dropped, HTML entities unescaped, whitespace collapsed."""
    text = re.sub(r"<[^>|]+\|([^>]+)>", r"\1", text)  # <thing|label> -> label
    text = re.sub(r"<(https?://[^>]+)>", r"\1", text)  # <url> -> url
    text = re.sub(r"<[^>]*>", "", text)  # bare <@U…>, <!here>, …
    return " ".join(html.unescape(text).split()).strip()


@register_source
class SlackSource(Source):
    """Summarize a Slack channel's recent messages."""

    name = "slack"

    def __init__(
        self,
        channel: str,
        token: str | None = None,
        max_count: int = 25,
        endpoint: str | None = None,
        get: Callable[[str], Any] | None = None,
    ) -> None:
        self.channel = channel.lstrip("#")
        self.token = token or source_token("slack")
        self.max_count = max_count
        self.base = (endpoint or source_endpoint("slack") or _API).rstrip("/")
        self._get = get or self._http_json
        self._users: dict[str, str] = {}

    def _http_json(self, url: str) -> Any:
        req = urllib.request.Request(
            url, headers={"User-Agent": "maelcom", "Authorization": f"Bearer {self.token}"}
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.load(resp)

    def _api(self, method: str, **params: Any) -> dict:
        query = urllib.parse.urlencode(params)
        return self._get(f"{self.base}/{method}?{query}") or {}

    def _channel_id(self) -> str | None:
        if _CHANNEL_ID.match(self.channel):  # already an ID
            return self.channel
        data = self._api("conversations.list", types="public_channel,private_channel", limit=1000)
        for chan in data.get("channels", []):
            if chan.get("name") == self.channel:
                return chan.get("id")
        return None

    def _user_name(self, uid: str) -> str:
        if uid not in self._users:
            user = (self._api("users.info", user=uid).get("user") or {})
            profile = user.get("profile") or {}
            self._users[uid] = (
                profile.get("display_name") or user.get("real_name") or user.get("name") or uid
            )
        return self._users[uid]

    def poll(self, since: datetime | None = None) -> list[NewsItem]:
        if not self.token:
            return []
        channel_id = self._channel_id()
        if not channel_id:
            return []
        history = self._api("conversations.history", channel=channel_id, limit=self.max_count)
        items: list[NewsItem] = []
        for msg in history.get("messages", []):
            if msg.get("subtype") or msg.get("bot_id"):  # joins / integrations / system
                continue
            text = _clean(msg.get("text", ""))
            if not text:
                continue
            uid = msg.get("user")
            ts = msg.get("ts")
            items.append(
                NewsItem(
                    id=f"slack:{channel_id}:{ts}",
                    source=self.name,
                    kind="message",
                    title=text,
                    origin=f"#{self.channel}",
                    actors=[self._user_name(uid)] if uid else [],
                    timestamp=datetime.fromtimestamp(float(ts), tz=UTC) if ts else None,
                    raw={"channel": channel_id, "ts": ts},
                )
            )
        return items
