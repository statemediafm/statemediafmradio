"""LLM client abstraction for the newsroom.

The summarizer depends on the ``LLMClient`` interface only. ``LiteLLMClient`` is
the default backend; ``FakeLLMClient`` backs the tests; ``stubs`` holds
placeholder backends for other proxies/harnesses. Model selection is config —
see ``model_config.yaml`` and :func:`load_model_config`.
"""

from __future__ import annotations

from .base import (
    DEFAULT_CONFIG_PATH,
    GATEWAY_PRESETS,
    LLMClient,
    LLMConfig,
    llm_config,
    load_model_config,
)
from .fake import FakeLLMClient
from .litellm_client import LiteLLMClient, discover_models, resolve_gateway

__all__ = [
    "DEFAULT_CONFIG_PATH",
    "GATEWAY_PRESETS",
    "FakeLLMClient",
    "LLMClient",
    "LLMConfig",
    "LiteLLMClient",
    "discover_models",
    "llm_config",
    "load_model_config",
    "resolve_gateway",
]
