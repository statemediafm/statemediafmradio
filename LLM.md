# LLM / model gateway — config scaffold

Maelcom's news parsing (the `--live` summarizer, and future LLM paths) goes
through the provider-neutral `LLMClient`. The default is `LiteLLMClient`, whose
model, gateway URL and key are all **config**, not code. In development it points
at the local Claude client; cutting over to a gateway is a config change.

Two knobs:

1. **`[llm]` config section** (in your `--config` file) — picks the model /
   overrides. Parsed by `roster.llm_settings()` → `newsroom.llm.llm_config()`.
2. **`llm-gateway` auth slot** (Settings tab) — the gateway **base URL** +
   **API key**. `LiteLLMClient` uses these for `api_base`/`api_key` when the
   config doesn't set them, so you can keep credentials out of config files.

The gateway is **provider-agnostic**: anything OpenAI-compatible works with the
same slot + an `openai/…` model string.

```toml
[llm]
# Base on a model_config.yaml profile, and/or set fields inline (inline wins):
# profile = "dev"
model   = "openai/gpt-4o-mini"
# api_base / api_key_env optional — omit to use the llm-gateway auth slot:
# api_base    = "https://your-gateway/v1"
# api_key_env = "MY_KEY"
temperature = 1
max_tokens  = 1024
```

## Per-provider scaffold

**Azure OpenAI** — model = your deployment; `api_version` in `extra`. Endpoint +
key from the `llm-gateway` slot (or set here / `AZURE_API_KEY`).
```toml
[llm]
model    = "azure/<deployment-name>"
api_base = "https://<resource>.openai.azure.com"
[llm.extra]
api_version = "2024-06-01"
```

**OpenRouter** — one endpoint to many models.
```toml
[llm]
model       = "openrouter/anthropic/claude-3.5-sonnet"
api_key_env = "OPENROUTER_API_KEY"   # or use the llm-gateway slot
```

**TrueFoundry (or any OpenAI-compatible gateway)** — model = the gateway's model
name; leave `api_base`/key to the `llm-gateway` auth slot.
```toml
[llm]
model = "openai/<gateway-model-name>"
```

**vLLM** (self-hosted, OpenAI-compatible).
```toml
[llm]
model    = "openai/<served-model>"
api_base = "http://localhost:8000/v1"   # or the llm-gateway slot
```

**Ollama** (local).
```toml
[llm]
model    = "ollama/llama3.1"
api_base = "http://localhost:11434"
```

**NVIDIA NIM** (OpenAI-compatible microservice).
```toml
[llm]
model    = "openai/meta/llama-3.1-8b-instruct"
api_base = "http://localhost:8000/v1"
api_key_env = "NIM_API_KEY"
```

**LangSmith** — observability/tracing, **not** a model backend, so it isn't an
`[llm]` profile. LiteLLM emits traces to LangSmith via environment variables; set
them in your shell / process env (never committed):
```
LANGSMITH_TRACING=true
LANGSMITH_API_KEY=<key>
LANGSMITH_PROJECT=maelcom
```

## How it resolves

- `--live` builds the config from `[llm]` (from `--config`) overlaid with
  `--profile`, else the `model_config.yaml` profile.
- `LiteLLMClient` then fills any missing `api_base`/`api_key` from the
  `llm-gateway` auth slot. Explicit config/env always win.
- Nothing set → the local dev model (`model_config.yaml` `default: dev`,
  Anthropic key from the environment) — unchanged.
