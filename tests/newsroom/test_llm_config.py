"""Tests for llm_config() — building an LLMConfig from an [llm] config table."""

from __future__ import annotations

import pytest

from maelcom.newsroom.llm import llm_config

CONFIG_YAML = """
default: dev
profiles:
  dev:
    model: anthropic/claude-opus-4-8
    temperature: 1
    max_tokens: 1024
  fast:
    model: openai/gpt-4o-mini
    api_base: https://gw/v1
    extra:
      api_version: "2024-06-01"
"""


@pytest.fixture
def cfg_path(tmp_path):
    p = tmp_path / "model_config.yaml"
    p.write_text(CONFIG_YAML)
    return p


def test_empty_settings_uses_default_profile(cfg_path):
    cfg = llm_config({}, path=cfg_path)
    assert cfg.model == "anthropic/claude-opus-4-8"
    assert cfg.max_tokens == 1024


def test_profile_key_selects_profile(cfg_path):
    cfg = llm_config({"profile": "fast"}, path=cfg_path)
    assert cfg.model == "openai/gpt-4o-mini"
    assert cfg.api_base == "https://gw/v1"
    assert cfg.extra["api_version"] == "2024-06-01"


def test_inline_model_needs_no_profile(cfg_path):
    # A table naming its own model is self-contained; the profile file is untouched.
    cfg = llm_config({"model": "ollama/llama3.1", "api_base": "http://localhost:11434"},
                     path=cfg_path)
    assert cfg.model == "ollama/llama3.1"
    assert cfg.api_base == "http://localhost:11434"


def test_inline_fields_override_the_profile(cfg_path):
    cfg = llm_config({"profile": "fast", "temperature": 0.2}, path=cfg_path)
    assert cfg.model == "openai/gpt-4o-mini"  # kept from profile
    assert cfg.api_base == "https://gw/v1"    # kept from profile
    assert cfg.temperature == 0.2             # overridden inline
    assert cfg.extra["api_version"] == "2024-06-01"  # base extra preserved


def test_unknown_keys_go_to_extra(cfg_path):
    cfg = llm_config({"model": "openai/x", "top_p": 0.9}, path=cfg_path)
    assert cfg.extra["top_p"] == 0.9
