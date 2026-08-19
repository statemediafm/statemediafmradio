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

from .auth import source_endpoint, source_token
from .core.schedule import Cadence, parse_duration
from .sources import (
    FORGE_DEFAULT_MAX_AGE,
    HackerNewsSource,
    JiraSource,
    PagerDutySource,
    SlackSource,
    Source,
    detect_forge,
    open_source,
)


def load_config(path: str | Path) -> dict:
    """Load a roster config from a ``.toml`` (default) or ``.json`` file."""
    path = Path(path)
    raw = path.read_bytes()
    if path.suffix == ".json":
        return json.loads(raw)
    return tomllib.loads(raw.decode("utf-8"))


_DEFAULT_GENERATOR = "Entrainment 0.1"


def llm_settings(config: dict) -> dict:
    """The ``[llm]`` config table (model/gateway selection for news parsing), or
    ``{}`` when absent. Passed to ``newsroom.llm.llm_config`` to build an
    ``LLMConfig``. See LLM.md for the per-provider scaffold."""
    return dict(config.get("llm", {})) if isinstance(config, dict) else {}


def genmusic_settings(config: dict) -> dict:
    """The ``[genmusic]`` config: which ambient generator to use, whether to show
    the UI selector (on by default; set ``selector = false`` to hide it), and an
    optional dir of user/contributor generators to load. Sensible defaults when
    the section is absent.
    """
    g = config.get("genmusic", {}) if isinstance(config, dict) else {}
    return {
        "generator": g.get("generator", _DEFAULT_GENERATOR),
        "selector": bool(g.get("selector", True)),
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


def source_kinds() -> list[str]:
    """The registered source kinds (for the live source-management UI)."""
    return sorted(_SOURCE_KINDS)


def _build_hackernews(topic: str, seg: dict) -> Source:
    return HackerNewsSource(max_count=int(seg.get("max_count", 25)))


def _build_repo(topic: str, seg: dict) -> Source:
    repo = seg.get("repo")
    if not repo:
        raise ValueError(f"segment {topic!r}: source='repo' needs a 'repo' URL or path")
    # Self-hosted GitLab: the configured instance base URL ([gitlab] endpoint),
    # so its URLs are recognized as GitLab and polled via its API. A per-segment
    # 'gitlab_base' overrides it (rare); blank → gitlab.com.
    gitlab_base = seg.get("gitlab_base") or source_endpoint("gitlab") or None
    # Token precedence: an explicit token, then token_env, else the gitignored
    # auth config for the detected forge (github/gitlab), else none.
    token = seg.get("token") or (os.environ.get(seg["token_env"]) if seg.get("token_env") else None)
    if token is None:
        forge = detect_forge(repo, gitlab_base=gitlab_base)  # (platform, slug) | None
        if forge is not None and forge[0] in ("github", "gitlab"):
            token = source_token(forge[0])
    # max_age (e.g. "60d", "48h") caps a forge to items opened within the window;
    # omitted → the 60-day default (see ForgeSource).
    max_age = parse_duration(seg["max_age"]) if seg.get("max_age") else FORGE_DEFAULT_MAX_AGE
    return open_source(
        repo,
        max_count=int(seg.get("max_count", 25)),
        token=token,
        max_age=max_age,
        gitlab_base=gitlab_base,
    )


def _build_slack(topic: str, seg: dict) -> Source:
    channel = seg.get("channel")
    if not channel:
        raise ValueError(f"segment {topic!r}: source='slack' needs a 'channel'")
    return SlackSource(channel, max_count=int(seg.get("max_count", 25)))


def _build_jira(topic: str, seg: dict) -> Source:
    project = seg.get("project")
    if not project:
        raise ValueError(f"segment {topic!r}: source='jira' needs a 'project'")
    return JiraSource(project, max_count=int(seg.get("max_count", 25)))


def _build_pagerduty(topic: str, seg: dict) -> Source:
    statuses = seg.get("statuses") or ("triggered", "acknowledged")
    return PagerDutySource(statuses=tuple(statuses), max_count=int(seg.get("max_count", 25)))


register_source_kind("hackernews", _build_hackernews)
register_source_kind("hn", _build_hackernews)
register_source_kind("repo", _build_repo)
register_source_kind("slack", _build_slack)
register_source_kind("jira", _build_jira)
register_source_kind("pagerduty", _build_pagerduty)


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


def build_segment(seg: dict, index: int = 0) -> tuple[str, Source, Cadence, int | None]:
    """Build one roster entry ``(topic, source, cadence, headlines)`` from a
    segment dict — the unit the live source-management UI adds/removes. ``index``
    only names an untitled segment. Raises ``ValueError`` for a malformed segment.
    """
    topic = seg.get("topic") or f"Segment {index + 1}"
    cadence = Cadence(
        parse_duration(seg.get("every", "15m")),
        parse_duration(seg.get("offset", 0)),
    )
    headlines = int(seg["headlines"]) if "headlines" in seg else None
    return (topic, _build_source(topic, seg), cadence, headlines)


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
    return [build_segment(seg, i) for i, seg in enumerate(segments)]
