"""Persisted, **non-secret** station settings — the Settings tab's memory.

The zero-config goal (ZERO_CONFIG_PLAN.md) is: run with no flags, configure
everything in the Settings UI, and have it survive a restart. This module is the
persistence spine. It stores the operator's UI choices — the ambient generator,
voice, energy, mix toggles, and the source roster — in ``statemediafm.config.toml``
(cwd; ``$STATEMEDIAFM_CONFIG`` overrides).

**Secrets never live here.** GitLab PATs and the LLM-gateway key stay in
``statemediafm.auth.toml`` (see ``auth.py``); persisted sources reference their
provider, not a literal token. Stdlib only (``tomllib`` to read, a small targeted
writer below), so the zero-dependency core is unchanged.
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path


def config_path(path: str | os.PathLike | None = None) -> Path:
    """Where the persisted settings live. ``$STATEMEDIAFM_CONFIG`` overrides;
    otherwise ``statemediafm.config.toml`` in the working directory."""
    if path is not None:
        return Path(path)
    return Path(os.environ.get("STATEMEDIAFM_CONFIG", "statemediafm.config.toml"))


def load_config(path: str | os.PathLike | None = None) -> dict:
    """The parsed settings, or ``{}`` if absent/unreadable (so a missing or
    corrupt file degrades to built-in defaults rather than failing to boot)."""
    p = config_path(path)
    if not p.is_file():
        return {}
    try:
        return tomllib.loads(p.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return {}


# ── Writing (a small serializer for this known schema — stdlib only) ──────────


def _fmt(value: object) -> str:
    if isinstance(value, bool):  # before int — bool is a subclass of int
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, str):
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_fmt(v) for v in value) + "]"
    raise TypeError(f"config value not serializable: {value!r}")


def _dump(data: dict) -> str:
    """Serialize the settings dict — the ``[station]``/``[mix]`` tables and the
    ``[[sources]]`` array-of-tables — to TOML."""
    lines: list[str] = []
    for section in ("station", "news", "mix"):
        table = data.get(section)
        if isinstance(table, dict) and table:
            lines.append(f"[{section}]")
            for key in sorted(table):
                val = table[key]
                if val is None:
                    continue
                lines.append(f"{key} = {_fmt(val)}")
            lines.append("")
    for seg in data.get("sources", []) or []:
        lines.append("[[sources]]")
        for key in seg:
            val = seg[key]
            if val is None:
                continue
            lines.append(f"{key} = {_fmt(val)}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def save_config(data: dict, path: str | os.PathLike | None = None) -> Path:
    """Write the settings atomically (temp file + rename, so a crash mid-write
    can't truncate the config). Non-secret, so no chmod."""
    p = config_path(path)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(_dump(data), encoding="utf-8")
    os.replace(tmp, p)
    return p


# ── State ↔ config ────────────────────────────────────────────────────────────


def state_to_config(state) -> dict:
    """Snapshot the persistable fields of a serve ``_State`` into a config dict.
    Demo-Mode sources are transient and deliberately excluded."""
    demo = set(getattr(state, "demo_topics", []) or [])
    sources = [dict(s) for s in getattr(state, "segments", []) or [] if s.get("topic") not in demo]
    news = {
        "live": bool(getattr(state, "live", False)),
        "model": getattr(state, "news_model", None) or "",
    }
    if getattr(state, "news_temperature", None) is not None:
        news["temperature"] = float(state.news_temperature)
    if getattr(state, "news_max_tokens", None) is not None:
        news["max_tokens"] = int(state.news_max_tokens)
    station = {
        "generator": getattr(state, "model", None),
        "voice": getattr(state, "voice", None),
        "style": getattr(state, "style", None),
        "base_intensity": float(getattr(state, "base_intensity", 0.25)),
        "quiet_mode": bool(getattr(state, "quiet_mode", False)),
        "refresh_s": float(getattr(state, "refresh_s", 60.0)),
    }
    if getattr(state, "news_every_s", None) is not None:
        station["news_every_s"] = float(state.news_every_s)
    return {
        "station": station,
        "news": news,
        "mix": {
            "generators": bool(getattr(state, "mix_generators", False)),
            "models": list(getattr(state, "mix_models", []) or []),
            "spotify": bool(getattr(state, "mix_spotify", False)),
        },
        "sources": sources,
    }


def apply_station(state, cfg: dict) -> None:
    """Seed a ``_State`` with the persisted ``[station]``/``[mix]`` settings.

    The **roster** (``[[sources]]``) is applied by the CLI via ``build_segment``,
    and the ambient **generator**/**voice**/**style** are resolved there with
    flag precedence — so this only restores the fields with no flag: energy, quiet
    mode, and the mix toggles."""
    if not cfg:
        return
    station = cfg.get("station", {}) or {}
    if "base_intensity" in station:
        state.base_intensity = float(station["base_intensity"])
    if "quiet_mode" in station:
        state.quiet_mode = bool(station["quiet_mode"])
    mix = cfg.get("mix", {}) or {}
    if "generators" in mix:
        state.mix_generators = bool(mix["generators"])
    if "models" in mix:
        state.mix_models = list(mix["models"])
    if "spotify" in mix:
        state.mix_spotify = bool(mix["spotify"])
