"""Deterministic, offline ``LLMClient`` for tests and demos.

No network, no provider dependency, stable output for a given prompt — so the
summarization pipeline and the broadcast plan can be exercised end-to-end in CI
without credentials or a live model.
"""

from __future__ import annotations

from .base import LLMClient, LLMConfig


class FakeLLMClient(LLMClient):
    """Echo a truncated, labelled stand-in for a real completion.

    The output is a pure function of ``prompt`` and ``cfg.model`` so tests can
    assert on it exactly.
    """

    def __init__(self, max_chars: int = 280) -> None:
        self.max_chars = max_chars

    def complete(self, prompt: str, cfg: LLMConfig) -> str:
        excerpt = " ".join(prompt.split())[: self.max_chars]
        return f"[fake:{cfg.model}] {excerpt}"
