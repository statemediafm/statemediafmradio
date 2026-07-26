"""LLM client abstraction + model-config schema.

The newsroom summarizer depends only on the ``LLMClient`` interface below — it
never imports a provider SDK directly. This keeps the summarization pipeline
provider-neutral and testable: swap the backend (LiteLLM, a direct SDK, a local
harness) or the model (dev Claude client vs a self-hosted model) without
touching the pipeline. Model selection lives entirely in ``LLMConfig`` /
``model_config.yaml``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# model_config.yaml sits at the newsroom package root (parent of this package).
DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "model_config.yaml"


@dataclass(frozen=True, slots=True)
class LLMConfig:
    """A single model-selection profile: the LiteLLM-parameter surface.

    ``model`` is a LiteLLM model string (e.g. ``anthropic/claude-opus-4-8`` for
    the local Claude client, or ``openai/local-model`` for a self-hosted
    endpoint). ``api_key_env`` names the environment variable to read the key
    from; when unset, the backend resolves credentials itself (for the Anthropic
    provider that means the ``ANTHROPIC_API_KEY`` or ``ANTHROPIC_AUTH_TOKEN``
    environment variable — LiteLLM does not read a local ``ant auth login``
    profile). ``extra`` passes any additional LiteLLM keyword through untouched.
    """

    model: str
    api_base: str | None = None
    api_key_env: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    timeout: float | None = None
    extra: dict[str, Any] = field(default_factory=dict)


# Quick-fill presets for the Settings tab's llm-gateway row — the OpenAI-
# compatible gateways from LLM.md. ``api_base`` seeds the endpoint field (blank
# where it's install-specific) and ``model`` an example model string. Not
# credentials — the key still goes in the auth slot. See LLM.md.
GATEWAY_PRESETS: list[dict[str, str]] = [
    {"name": "LiteLLM", "api_base": "http://localhost:4000",
     "model": "openai/<litellm-model-name>"},
    {"name": "Azure OpenAI", "api_base": "https://<resource>.openai.azure.com",
     "model": "azure/<deployment-name>"},
    {"name": "OpenRouter", "api_base": "https://openrouter.ai/api/v1",
     "model": "openrouter/anthropic/claude-3.5-sonnet"},
    {"name": "TrueFoundry", "api_base": "", "model": "openai/<gateway-model-name>"},
    {"name": "vLLM", "api_base": "http://localhost:8000/v1", "model": "openai/<served-model>"},
    {"name": "Ollama", "api_base": "http://localhost:11434", "model": "ollama/llama3.1"},
    {"name": "NVIDIA NIM", "api_base": "http://localhost:8000/v1",
     "model": "openai/meta/llama-3.1-8b-instruct"},
]


class LLMClient(ABC):
    """Provider-neutral completion primitive.

    Implementations must be side-effect-free apart from the model call itself,
    so a summarizer built on this interface can be exercised with the
    deterministic ``FakeLLMClient`` and no network access.
    """

    @abstractmethod
    def complete(self, prompt: str, cfg: LLMConfig) -> str:
        """Return the model's text completion for ``prompt`` under ``cfg``."""
        raise NotImplementedError


def load_model_config(
    profile: str | None = None,
    path: Path | str = DEFAULT_CONFIG_PATH,
) -> LLMConfig:
    """Load one profile from ``model_config.yaml`` into an ``LLMConfig``.

    Passing ``profile=None`` selects the file's ``default`` profile. Any key
    not recognised as a first-class ``LLMConfig`` field is collected into
    ``extra`` and forwarded to the backend verbatim.
    """
    import yaml  # lazy: keep the package importable without PyYAML installed

    data = yaml.safe_load(Path(path).read_text()) or {}
    profiles: dict[str, dict[str, Any]] = data.get("profiles", {})
    name = profile or data.get("default")
    if not name:
        raise ValueError(f"No profile requested and no 'default' set in {path}")
    if name not in profiles:
        raise KeyError(f"Profile {name!r} not found in {path} (have: {sorted(profiles)})")

    return _config_from_dict(profiles[name])


def _config_from_dict(raw: dict[str, Any]) -> LLMConfig:
    """Build an ``LLMConfig`` from a raw dict, collecting unknown keys into
    ``extra`` (forwarded to LiteLLM verbatim)."""
    raw = dict(raw)
    known = {f for f in LLMConfig.__dataclass_fields__ if f != "extra"}
    extra = {**raw.pop("extra", {}), **{k: raw.pop(k) for k in list(raw) if k not in known}}
    return LLMConfig(**raw, extra=extra)


def llm_config(
    settings: dict | None = None,
    *,
    profile: str | None = None,
    path: Path | str = DEFAULT_CONFIG_PATH,
) -> LLMConfig:
    """Build an ``LLMConfig`` from an ``[llm]`` config table.

    The table may name a ``profile`` (a ``model_config.yaml`` profile to base on)
    and/or inline fields (``model``, ``api_base``, ``api_key_env``, ``temperature``
    …) which override the profile. When it names a ``model``, no profile is needed.
    A ``models`` list (the UI's selectable options) is ignored here — it is not a
    LiteLLM parameter.
    Falls back to the default profile when the table is empty. The gateway base URL
    and key can be left out entirely — they fall back to the ``llm-gateway`` auth
    slot at call time (see ``LiteLLMClient``).
    """
    settings = dict(settings or {})
    prof = settings.pop("profile", None) or profile
    settings.pop("models", None)  # UI-only: the selectable model list, not a litellm param
    if "model" in settings:
        return _config_from_dict(settings)
    base = load_model_config(prof, path)
    if not settings:
        return base
    merged = {
        **{f: getattr(base, f) for f in LLMConfig.__dataclass_fields__ if f != "extra"},
        **base.extra,
        **settings,
    }
    return _config_from_dict({k: v for k, v in merged.items() if v is not None})
