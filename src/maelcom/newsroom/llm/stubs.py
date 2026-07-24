"""Stub ``LLMClient`` backends for other proxies and harnesses.

These satisfy the interface but are not implemented — they mark wiring points
for alternative backends so a future integration is a matter of filling in one
``complete`` method, not reshaping the pipeline. LiteLLM (see
``litellm_client.py``) already covers most providers via config; reach for one
of these only when a backend needs bespoke handling LiteLLM can't express.
"""

from __future__ import annotations

from .base import LLMClient, LLMConfig


class AnthropicSDKClient(LLMClient):
    """Call Claude through the official ``anthropic`` SDK directly.

    For when native Anthropic features (adaptive thinking, effort, prompt
    caching) are wanted without routing through LiteLLM.
    """

    def complete(self, prompt: str, cfg: LLMConfig) -> str:
        raise NotImplementedError("AnthropicSDKClient is not implemented yet")


class OpenAICompatibleProxyClient(LLMClient):
    """Talk to an OpenAI-compatible proxy (vLLM, LiteLLM proxy server, etc.)."""

    def complete(self, prompt: str, cfg: LLMConfig) -> str:
        raise NotImplementedError("OpenAICompatibleProxyClient is not implemented yet")


class LocalHarnessClient(LLMClient):
    """Drive a locally hosted inference harness / self-hosted model runtime."""

    def complete(self, prompt: str, cfg: LLMConfig) -> str:
        raise NotImplementedError("LocalHarnessClient is not implemented yet")
