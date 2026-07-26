"""Per-source endpoints and personal auth tokens — a **gitignored** local config.

The Settings tab writes here; sources read their tokens/endpoints from here. This
file holds secrets, so it is gitignored and never committed; it is written
owner-only (0600). Localhost admin config for a single install.

Path: ``$STATEMEDIAFM_AUTH`` or ``statemediafm.auth.toml`` in the working directory.
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path

# Entries the Settings tab exposes (endpoint optional per entry). Mostly news
# sources; ``llm-gateway`` is the LLM/model gateway (endpoint = the gateway base
# URL, token = the gateway API key) used for news parsing — provider-agnostic:
# LiteLLM, OpenRouter, Azure OpenAI, a self-hosted vLLM/Ollama/NIM, etc.
AUTH_SOURCES = ("github", "gitlab", "jira", "slack", "pagerduty", "llm-gateway")


def auth_path() -> Path:
    return Path(os.environ.get("STATEMEDIAFM_AUTH", "statemediafm.auth.toml"))


def load_auth(path: str | Path | None = None) -> dict:
    """The parsed auth config, or ``{}`` if absent/unreadable."""
    p = Path(path) if path else auth_path()
    if not p.is_file():
        return {}
    try:
        return tomllib.loads(p.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return {}


def _esc(value: object) -> str:
    return str(value).replace("\\", "\\\\").replace('"', '\\"')


def _dump_toml(data: dict) -> str:
    lines: list[str] = []
    for section in sorted(data):
        entry = data[section]
        if not isinstance(entry, dict):
            continue
        lines.append(f"[{section}]")
        for key in sorted(entry):
            val = entry[key]
            if val in (None, ""):
                continue
            lines.append(f'{key} = "{_esc(val)}"')
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def save_auth_entry(
    source: str,
    *,
    endpoint: str | None = None,
    token: str | None = None,
    path: str | Path | None = None,
) -> None:
    """Merge ``endpoint``/``token`` for ``source`` into the gitignored auth file.
    The token is only overwritten when a non-empty new value is supplied (so the
    UI can save an endpoint change without re-entering the token)."""
    p = Path(path) if path else auth_path()
    data = load_auth(p)
    entry = dict(data.get(source, {}))
    if endpoint is not None:
        entry["endpoint"] = endpoint.strip()
    if token:
        entry["token"] = token.strip()
    data[source] = entry
    p.write_text(_dump_toml(data), encoding="utf-8")
    try:
        p.chmod(0o600)  # tokens: owner-only
    except OSError:
        pass


def _hint(token: object) -> str:
    if not token:
        return ""
    t = str(token)
    return "…" + t[-4:] if len(t) > 4 else "••••"


def masked_auth(path: str | Path | None = None) -> dict:
    """Per-source view for the UI — endpoints in the clear, tokens masked."""
    data = load_auth(path)
    return {
        src: {
            "endpoint": (data.get(src) or {}).get("endpoint", ""),
            "token_set": bool((data.get(src) or {}).get("token")),
            "token_hint": _hint((data.get(src) or {}).get("token")),
        }
        for src in AUTH_SOURCES
    }


def source_token(source: str, path: str | Path | None = None) -> str | None:
    return (load_auth(path).get(source) or {}).get("token")


def source_endpoint(source: str, path: str | Path | None = None) -> str | None:
    return (load_auth(path).get(source) or {}).get("endpoint")
