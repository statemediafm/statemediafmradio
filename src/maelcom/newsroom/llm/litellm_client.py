"""Default ``LLMClient`` backend: a thin wrapper over ``litellm.completion``.

Provider-neutral by construction — the target model, endpoint, and credentials
all come from ``LLMConfig``. In development the default profile points at the
local Claude client (``anthropic/claude-opus-4-8``); pointing at a self-hosted
model later is a ``model_config.yaml`` edit, not a code change.
"""

from __future__ import annotations

import os
from typing import Any

from ...auth import source_endpoint, source_token
from .base import LLMClient, LLMConfig


class LiteLLMClient(LLMClient):
    """Send a single-message chat completion through LiteLLM."""

    def complete(self, prompt: str, cfg: LLMConfig) -> str:
        import litellm  # lazy: only required when this backend is actually used

        kwargs = self._build_kwargs(cfg)
        response = litellm.completion(
            messages=[{"role": "user", "content": prompt}],
            **kwargs,
        )
        return response.choices[0].message.content or ""

    @staticmethod
    def _build_kwargs(cfg: LLMConfig) -> dict[str, Any]:
        """Translate an ``LLMConfig`` into LiteLLM ``completion`` kwargs.

        ``None`` values are dropped so LiteLLM/the provider apply their own
        defaults. The base URL and key fall back to the gitignored ``litellm``
        auth entry (Settings tab) when the config doesn't set them — so an install
        can cut over from the local model to a LiteLLM (or any OpenAI-compatible)
        gateway just by entering its endpoint + key, no env vars or code change.
        Otherwise, when ``api_key_env`` is unset the backend resolves credentials
        itself — for the Anthropic provider that means ``ANTHROPIC_API_KEY`` /
        ``ANTHROPIC_AUTH_TOKEN`` (LiteLLM does not read an ``ant auth login``
        profile). Explicit config always wins over the auth fallback.
        """
        kwargs: dict[str, Any] = {"model": cfg.model}
        api_base = cfg.api_base or source_endpoint("llm-gateway")
        if api_base:
            kwargs["api_base"] = api_base
        if cfg.temperature is not None:
            kwargs["temperature"] = cfg.temperature
        if cfg.max_tokens is not None:
            kwargs["max_tokens"] = cfg.max_tokens
        if cfg.timeout is not None:
            kwargs["timeout"] = cfg.timeout
        api_key = os.environ.get(cfg.api_key_env) if cfg.api_key_env else None
        api_key = api_key or source_token("llm-gateway")
        if api_key:
            kwargs["api_key"] = api_key
        kwargs.update(cfg.extra)
        return kwargs
