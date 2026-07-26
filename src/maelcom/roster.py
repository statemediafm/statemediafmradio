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

import importlib
import json
import os
import sys
import tomllib
from collections.abc import Callable
from pathlib import Path

from .auth import source_token
from .core.schedule import Cadence, parse_duration
from .sources import HackerNewsSource, SlackSource, Source, detect_forge, open_source


def load_config(path: str | Path) -> dict:
    """Load a roster config from a ``.toml`` (default) or ``.json`` file."""
    path = Path(path)
    raw = path.read_bytes()
    if path.suffix == ".json":
        return json.loads(raw)
    return tomllib.loads(raw.decode("utf-8"))


_DEFAULT_GENERATOR = "Entrainment 0.1"


def genmusic_settings(config: dict) -> dict:
    """The ``[genmusic]`` config: which ambient generator to use, whether to show
    the UI selector (off by default), and an optional dir of user/contributor
    generators to load. Sensible defaults when the section is absent.
    """
    g = config.get("genmusic", {}) if isinstance(config, dict) else {}
    return {
        "generator": g.get("generator", _DEFAULT_GENERATOR),
        "selector": bool(g.get("selector", False)),
        "generators_dir": g.get("generators"),
    }


# ── Source-kind harness ─────────────────────────────────────────────────────
# A registry of source *kinds* (the `source = "…"` in a segment) → a builder
# `build(topic, seg) -> Source`. Built-ins are registered below; installs add
# more in code via register_source_kind(), or from config via [[source_plugins]]
# (kind + a "module:function" builder). See SOURCES.md.
_SOURCE_KINDS: dict[str, Callable[[str, dict], Source]] = {}


def register_source_kind(kind: str, build: Callable[[str, dict], Source]) -> None:
    _SOURCE_KINDS[kind] = build


def _build_hackernews(topic: str, seg: dict) -> Source:
    return HackerNewsSource(max_count=int(seg.get("max_count", 25)))


def _build_repo(topic: str, seg: dict) -> Source:
    repo = seg.get("repo")
    if not repo:
        raise ValueError(f"segment {topic!r}: source='repo' needs a 'repo' URL or path")
    # Token precedence: an explicit token_env, else the gitignored auth config for
    # the detected forge (github/gitlab), else none.
    token = os.environ.get(seg["token_env"]) if seg.get("token_env") else None
    if token is None:
        forge = detect_forge(repo)  # (platform, slug) | None
        if forge is not None and forge[0] in ("github", "gitlab"):
            token = source_token(forge[0])
    return open_source(repo, max_count=int(seg.get("max_count", 25)), token=token)


def _build_slack(topic: str, seg: dict) -> Source:
    channel = seg.get("channel")
    if not channel:
        raise ValueError(f"segment {topic!r}: source='slack' needs a 'channel'")
    return SlackSource(channel, max_count=int(seg.get("max_count", 25)))


register_source_kind("hackernews", _build_hackernews)
register_source_kind("hn", _build_hackernews)
register_source_kind("repo", _build_repo)
register_source_kind("slack", _build_slack)


def load_source_plugins(config: dict) -> list[str]:
    """Register custom source kinds declared in ``[[source_plugins]]`` — each a
    ``{kind, builder="module:function"}``. Returns the kinds registered; a bad
    builder is skipped with a note, not fatal."""
    registered: list[str] = []
    for plugin in config.get("source_plugins", []) or []:
        kind, path = plugin.get("kind"), plugin.get("builder")
        if not kind or not path:
            continue
        try:
            module_path, _, func = path.partition(":")
            register_source_kind(kind, getattr(importlib.import_module(module_path), func))
            registered.append(kind)
        except (ImportError, AttributeError) as exc:
            print(f"skipping source plugin {kind!r}: {exc}", file=sys.stderr)
    return registered


def _build_source(topic: str, seg: dict) -> Source:
    kind = seg.get("source")
    build = _SOURCE_KINDS.get(kind)
    if build is None:
        raise ValueError(
            f"segment {topic!r}: unknown source {kind!r} (have: {sorted(_SOURCE_KINDS)})"
        )
    return build(topic, seg)


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
