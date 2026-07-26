# LLM / model gateway — config scaffold

State Media FM's news parsing (the `--live` summarizer, and future LLM paths) goes
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
# models the Settings tab offers for live news-model selection (UI-only, not a
# LiteLLM parameter). The current `model` is always offered too.
models = ["openai/gpt-4o-mini", "anthropic/claude-3.5-sonnet", "ollama/llama3.1"]
```

## Live news-model selection

`statemediafm serve --live` runs the LLM as the news writer (the gateway *parses* the
activity into prose). The **Settings tab** then shows a **News-parsing model**
picker: choose one of the `[llm] models` above, type any model string the gateway
serves, or click **↻ Discover from gateway** to auto-populate the list from the
gateway itself. The change applies to the next news cycle — no restart. Without
`--live`, news is the deterministic offline copy and the picker is hidden. A
gateway error during a cycle degrades gracefully to the offline copy so the
station stays on air.

**Auto-discovery** queries the gateway's OpenAI-compatible catalogue
(`GET {api_base}/models`) using the `llm-gateway` base URL + key, and merges the
returned ids into the picker. It's best-effort and on-demand (never at startup),
so a slow or unreachable gateway never stalls the server — it just adds nothing.
Providers without a `/models` listing (e.g. the default direct-Anthropic path,
which has no gateway base URL) return nothing; use `[llm] models` or free text
there.

## Per-provider scaffold

**LiteLLM proxy** — the reference OpenAI-compatible gateway (run `litellm --config
…`). Point `api_base` at your proxy; `model` is the alias you gave it in the proxy
config. Endpoint + key from the `llm-gateway` slot (or set here).
```toml
[llm]
model    = "openai/<litellm-model-name>"
api_base = "http://localhost:4000"        # or the llm-gateway slot
```

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
LANGSMITH_PROJECT=statemediafm
```

## How it resolves

- `--live` builds the config from `[llm]` (from `--config`) overlaid with
  `--profile`, else the `model_config.yaml` profile.
- `LiteLLMClient` then fills any missing `api_base`/`api_key` from the
  `llm-gateway` auth slot. Explicit config/env always win.
- Nothing set → the local dev model (`model_config.yaml` `default: dev`,
  Anthropic key from the environment) — unchanged.
