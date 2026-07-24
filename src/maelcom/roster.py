"""Broadcast roster: build a list of (topic, source, cadence) from config.

A roster is a list of segments, each naming a source and its airing cadence, so
the ``broadcast`` rundown is fully configurable — which sources air, how often,
and staggered by what offset. Config is TOML or JSON (both stdlib, so this still
works in the zero-dependency zipapp).

Example TOML::

    [[segments]]
    topic = "Hacker News front page"
    source = "hackernews"
    max_count = 10
    every = "15m"
    offset = "6m"

    [[segments]]
    topic = "Engineering issues"
    source = "repo"
    repo = "https://github.com/meltano/meltano"
    token_env = "GITHUB_TOKEN"
    every = "15m"
    offset = "0"
"""

from __future__ import annotations

import json
import os
import tomllib
from pathlib import Path

from .core.schedule import Cadence, parse_duration
from .sources import HackerNewsSource, Source, open_source


def load_config(path: str | Path) -> dict:
    """Load a roster config from a ``.toml`` (default) or ``.json`` file."""
    path = Path(path)
    raw = path.read_bytes()
    if path.suffix == ".json":
        return json.loads(raw)
    return tomllib.loads(raw.decode("utf-8"))


def _build_source(topic: str, seg: dict) -> Source:
    kind = seg.get("source")
    max_count = int(seg.get("max_count", 25))
    if kind in ("hackernews", "hn"):
        return HackerNewsSource(max_count=max_count)
    if kind == "repo":
        repo = seg.get("repo")
        if not repo:
            raise ValueError(f"segment {topic!r}: source='repo' needs a 'repo' URL or path")
        token = os.environ.get(seg["token_env"]) if seg.get("token_env") else None
        return open_source(repo, max_count=max_count, token=token)
    raise ValueError(
        f"segment {topic!r}: unknown source {kind!r} (use 'hackernews' or 'repo')"
    )


def build_roster(config: dict) -> list[tuple[str, Source, Cadence, int | None]]:
    """Turn a parsed roster config into ``(topic, source, cadence, headlines)``.

    ``headlines`` is the per-segment cap on how many headlines that source reads,
    or ``None`` when the segment omits it (the caller supplies a default). Raises
    ``ValueError`` for a missing/empty ``segments`` list or a malformed segment.
    Sources are constructed but not polled, so this stays offline.
    """
    segments = config.get("segments")
    if not segments:
        raise ValueError("roster config has no 'segments'")

    roster: list[tuple[str, Source, Cadence, int | None]] = []
    for i, seg in enumerate(segments):
        topic = seg.get("topic") or f"Segment {i + 1}"
        cadence = Cadence(
            parse_duration(seg.get("every", "15m")),
            parse_duration(seg.get("offset", 0)),
        )
        headlines = int(seg["headlines"]) if "headlines" in seg else None
        roster.append((topic, _build_source(topic, seg), cadence, headlines))
    return roster
