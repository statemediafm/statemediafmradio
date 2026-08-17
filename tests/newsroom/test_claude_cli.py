"""Tests for the local Claude CLI news-writer backend (offline, stub binary)."""

from __future__ import annotations

import pytest

from statemediafm.newsroom.llm import ClaudeCliClient, LLMConfig


def _stub(tmp_path, body: str):
    p = tmp_path / "claude"
    p.write_text("#!/bin/sh\n" + body)
    p.chmod(0o755)
    return p


def test_claude_cli_passes_the_prompt_on_stdin(tmp_path):
    stub = _stub(tmp_path, "cat\n")  # echo stdin → stdout
    client = ClaudeCliClient(binary=str(stub))
    assert client.available() is True
    # A LiteLLM-style "provider/model" is not passed as --model (CLI default used).
    out = client.complete("write the news", LLMConfig(model="anthropic/claude-opus-4-8"))
    assert out == "write the news"


def test_claude_cli_raises_on_nonzero_exit(tmp_path):
    stub = _stub(tmp_path, "echo boom >&2\nexit 3\n")
    with pytest.raises(RuntimeError, match="failed"):
        ClaudeCliClient(binary=str(stub)).complete("x", LLMConfig(model=""))


def test_claude_cli_raises_on_empty_output(tmp_path):
    stub = _stub(tmp_path, "true\n")  # exit 0, no stdout
    with pytest.raises(RuntimeError, match="no output"):
        ClaudeCliClient(binary=str(stub)).complete("x", LLMConfig(model=""))


def test_claude_cli_missing_binary_reports_and_raises():
    client = ClaudeCliClient(binary="/nonexistent/claude-xyz")
    assert client.available() is False
    with pytest.raises(RuntimeError, match="not found"):
        client.complete("x", LLMConfig(model=""))
