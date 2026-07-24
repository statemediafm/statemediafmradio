"""Default ``LLMClient`` backend: a thin wrapper over ``litellm.completion``.

Provider-neutral by construction — the target model, endpoint, and credentials
all come from ``LLMConfig``. In development the default profile points at the
local Claude client (``anthropic/claude-opus-4-8``); pointing at a self-hosted
model later is a ``model_config.yaml`` edit, not a code change.
"""

from __future__ import annotations

import os
from typing import Any

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
        defaults. When ``api_key_env`` is unset (or the variable is absent) no
        key is passed, letting the backend resolve credentials itself — for the
        Anthropic provider that means the ``ANTHROPIC_API_KEY`` (or
        ``ANTHROPIC_AUTH_TOKEN``) environment variable. LiteLLM does not read a
        local ``ant auth login`` profile, so one of those must be set.
        """
        kwargs: dict[str, Any] = {"model": cfg.model}
        if cfg.api_base is not None:
            kwargs["api_base"] = cfg.api_base
        if cfg.temperature is not None:
            kwargs["temperature"] = cfg.temperature
        if cfg.max_tokens is not None:
            kwargs["max_tokens"] = cfg.max_tokens
        if cfg.timeout is not None:
            kwargs["timeout"] = cfg.timeout
        if cfg.api_key_env:
            key = os.environ.get(cfg.api_key_env)
            if key:
                kwargs["api_key"] = key
        kwargs.update(cfg.extra)
        return kwargs
