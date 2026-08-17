# Plan: clone-and-run with no flags — move configuration into the Settings UI

**Goal.** Someone clones the repo, installs, runs a single command with **no
flags**, and gets a working station in the browser — then configures *everything*
(sources, GitLab/gateway credentials, live LLM news, cadences, generator, voice)
from the **Settings** tab, with those choices **persisted** so they survive a
restart. Flags become optional overrides, not the primary interface.

```
git clone … && cd statemediafmradio
pip install -e ".[all]"
statemediafm            # ← no flags; opens the player + Settings
```

This doc is the design + phased plan. It does not change code yet.

---

## 1. Where we are today (the gap)

`serve` is driven by flags, and most runtime state is **in-memory only** — lost on
restart. The persistence split today:

| Persisted (survives restart) | In-memory only (lost on restart) |
|---|---|
| Tokens + endpoints — `statemediafm.auth.toml` (`auth.py`) | The **roster** (UI-added sources) — `app.py` `/sources` is explicitly "this session only" |
| Spotify refresh token (`auth.toml`) | Ambient **generator**, **voice**, **style**, tuning, base energy |
| License key (`statemediafm.license`) | **Live** on/off + **news model** + temperature/max_tokens |
| | Quiet/Demo mode, broadcasting on/off |

Every `serve` flag and its current disposition:

| Flag | Does | Target |
|---|---|---|
| `--hn`, `--repo`, `--max-count`, `--token` | Boot roster + forge auth | **UI** — News Update Sources + Config (persist the roster) |
| `--config FILE` | Roster (.toml/.json) | **Superseded** by the persisted config (auto-loaded); keep as an explicit override |
| `--live`, `--profile` | Enable LLM news + pick model profile | **UI** — a "Live news" toggle + News-parsing model (remove the startup-only 409 gate) |
| `--generator`/`--ambient` | Starting ambient generator | **UI** — already a dropdown; **persist** it |
| `--style` | Writing style | Already removed from UI; internal default only |
| `--voice` | Narration voice | **UI** (currently feature-flagged off); persist when re-enabled |
| `--news-every`, `--refresh`, `--every` | Cadences | **UI** — a Cadence section; persist |
| `--host`, `--port` | Network bind | **Stay flags** (a launch/deploy concern, not runtime) — but ship sane defaults |
| `--tone` | Force placeholder audio | Stay a flag (dev/CI) |

**Net:** the two hard blockers to "no flags" are (a) **no persisted settings/roster**
and (b) **live LLM news is startup-only** (`app.py` `/news-model` → 409 unless
`serve --live`). Everything else is defaulting + UI plumbing.

---

## 2. Design

### 2.1 A persisted settings store

Add a single **non-secret** settings file, separate from `auth.toml` (which stays
secrets-only):

- **File:** `statemediafm.config.toml` (cwd; `$STATEMEDIAFM_CONFIG` overrides).
  **Gitignored**; ship `examples/config.toml` as a documented template.
- **Writer/reader:** stdlib only — read with `tomllib`, write by extending the tiny
  `_dump_toml` already in `auth.py` (lift it to a shared `configstore.py`). Keeps the
  zero-dependency core intact.
- **Schema (v1):**
  ```toml
  [station]
  generator = "Entrainment 0.1"
  voice = "alan"
  news_every = "17m"
  refresh_s = 60
  base_intensity = 0.25
  quiet_mode = false

  [news]
  live = false                 # LLM news on/off (was --live)
  model = ""                   # gateway-served model string ("" → gateway default)
  temperature = 1.0
  max_tokens = 1024

  [[sources]]                  # the persisted roster (was --hn/--repo/--config)
  topic = "Hacker News front page"
  source = "hackernews"
  every = "15m"

  [[sources]]
  topic = "Engineering"
  source = "repo"
  repo = "https://gitlab.mycorp.com/team/app"
  every = "15m"
  ```
  Secrets are **never** written here — the GitLab PAT / gateway key stay in
  `auth.toml`; sources reference them by provider (as `_build_repo` already does).

### 2.2 Load order (precedence)

At `serve` boot and in the CLI: **flags > `--config` file > `statemediafm.config.toml`
> built-in defaults.** With no flags and no files, defaults produce a valid empty
station (music runs; news waits for a source).

### 2.3 Live writes from the UI

Every Settings mutation that today only touches `_State` also **writes the config
file** (debounced). The endpoints already exist (`/sources`, `/model`, `/mix`,
`/news-model`, `/intensity`, …); each gains a persist call. On boot, `_State` is
**seeded from the file**, so the UI shows what you last set.

- **Roster:** `/sources` add/remove updates `[[sources]]` and rebuilds the live
  roster (it already rebuilds in-memory; just persist + re-`build_segment`). This
  also fixes today's "added sources vanish on restart."

### 2.4 Live-news toggle (remove the startup gate)

Decouple LLM news from the `--live` flag (`app.py:506` 409, `serve.py:394` seeds
`news_model` only when `--live`):

- Add `state.live: bool` + `[news].live`. A **"Live news (LLM)"** switch in Settings
  sets it and persists.
- `refresh_once` builds the LLM client **lazily** when `live` is on **and** a gateway
  (or key) is configured; otherwise the deterministic copy (today's graceful
  fallback already covers a misconfigured gateway — `serve.py:118`).
- `/news-model` POST no longer 409s; it sets the model and persists. The panel is
  shown whenever `live` is on. The **model default** (`anthropic/claude-opus-4-8`)
  is only used if the operator hasn't picked one — surface "↻ Discover from gateway"
  prominently so a gateway-served model is chosen.

### 2.5 First-run experience

- `statemediafm` with **no subcommand** defaults to `serve` (argparse default).
- With no config: bind `127.0.0.1:8150`, start the default generator (music needs no
  config), and render a **Settings banner**: "No sources yet — add one under News
  Update Sources to start the news." Health/player work immediately.
- Optionally **auto-open the browser** to the bound URL on boot (`webbrowser.open`;
  suppress with `--no-open`).

### 2.6 Install ergonomics

- README: make the canonical line `pip install -e ".[all]"` (fixes the understated
  `[web]`-only serve note); `.[web]` alone → a clear runtime message if `[llm]`/`[tts]`
  are missing (already partly there for tts→tone).
- Keep `--host/--port/--tone` as flags; document that everything else lives in
  Settings.

---

## 3. Phased implementation

**Phase 1 — Persistence spine (unblocks everything).**
1. `configstore.py`: `load_config_file()/save_config_file()` (stdlib TOML r/w), shared
   `_dump_toml`. `examples/config.toml` + gitignore `statemediafm.config.toml`.
2. Seed `_State` from the file at `create_app`/`serve.run`; add a `persist(state)`
   helper. Wire persist into the existing mutation endpoints.
3. Persist + restore the **roster** (`[[sources]]`), rebuilding via `build_segment`.
   → *Deliverable:* UI-set sources/generator/voice/cadence survive restart.

**Phase 2 — Live-from-UI.**
4. `state.live` + `[news].live`; lazy LLM client in `refresh_once`; drop the
   `/news-model` 409; show the panel when live; persist model/temp/max_tokens.
   → *Deliverable:* toggle LLM news on and pick a model without restarting.

**Phase 3 — No-flags boot.**
5. Default subcommand → `serve`; load-order precedence; empty-state Settings banner;
   optional `webbrowser.open` + `--no-open`. Standardize default port.
   → *Deliverable:* `statemediafm` alone runs and is fully configurable in the UI.

**Phase 4 — Polish.**
6. Cadence section in Settings (news-every / refresh) + persist. README rewrite of the
   run/onboarding flow. `config.toml` round-trip test; a "fresh clone" smoke test.

---

## 4. Risks / decisions

- **Config vs. secrets split:** never write tokens to `config.toml`; keep them in
  `auth.toml`. Sources reference providers, not literals. (Already how `_build_repo`
  resolves tokens.)
- **Concurrent writes:** the refresh loop (thread) and UI both touch state; persist
  from the request thread only, write atomically (temp file + rename), and treat the
  file as last-writer-wins for a single operator.
- **Determinism/tests:** default `serve` must stay offline-friendly (no network until
  a source is added). Keep the zero-dependency core — TOML only, no new deps.
- **Security:** `config.toml` is non-secret but still per-install; gitignore it and
  ship an example. Control-API auth already gates who can change it.
- **Live model mismatch:** the biggest "looks configured but 401s" trap — mitigate
  with the Discover button + a clear "pick a gateway-served model" hint (§2.4).

## 5. Definition of done

Clone → `pip install -e ".[all]"` → `statemediafm` → browser opens →
Settings → Config (GitLab URL + PAT, gateway URL + key) → News Update Sources (add
the project) → toggle **Live news** → pick a model → **it works, and still works
after a restart**, with no flag ever typed.
