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


def test_models_list_is_ignored_by_llm_config(cfg_path):
    # `models` is a UI-only selectable list, not a LiteLLM parameter.
    cfg = llm_config({"model": "openai/x", "models": ["a", "b"]}, path=cfg_path)
    assert "models" not in cfg.extra


def test_discover_models_parses_openai_listing():
    from maelcom.newsroom.llm import LLMConfig, discover_models

    calls = {}

    def fake_get(url, key):
        calls["url"], calls["key"] = url, key
        return {"data": [{"id": "gpt-4o-mini"}, {"id": "claude-3.5"}, {"no_id": 1}]}

    cfg = LLMConfig(model="m", api_base="https://gw/v1", api_key_env=None)
    got = discover_models(cfg, get=fake_get)
    assert got == ["claude-3.5", "gpt-4o-mini"]  # sorted, id-less entry dropped
    assert calls["url"] == "https://gw/v1/models"


def test_discover_models_no_gateway_returns_empty(monkeypatch):
    from maelcom.newsroom.llm import LLMConfig, discover_models
    from maelcom.newsroom.llm import litellm_client as lc

    # No api_base and no llm-gateway auth entry → nothing to query.
    monkeypatch.setattr(lc, "source_endpoint", lambda name, path=None: None)
    monkeypatch.setattr(lc, "source_token", lambda name, path=None: None)
    assert discover_models(LLMConfig(model="anthropic/claude")) == []


def test_discover_models_swallows_errors():
    from maelcom.newsroom.llm import LLMConfig, discover_models

    def boom(url, key):
        raise OSError("gateway unreachable")

    cfg = LLMConfig(model="m", api_base="https://gw/v1")
    assert discover_models(cfg, get=boom) == []
