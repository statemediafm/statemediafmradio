# State Media FM — Development Plan

> A self-hostable, multi-tenant **internal radio station** that turns a team's
> collaboration exhaust (git, Jira, Slack, Grafana, …) into a continuous,
> voiced broadcast — news summaries, generative music that tracks project
> activity, and occasional familiar songs — on a 2–5 minute "rhythm of the day."

This document is the working development plan. It is meant to be edited as the
project evolves. Section 7 (Roadmap) is the part to execute against;
everything before it is context and contracts.

---

## 1. Guiding principles

1. **Pillars first, then an integration core.** Each core feature is a
   standalone module with a narrow, documented interface and its own tests. A
   thin orchestration layer wires them together. No pillar imports another
   pillar's internals — only their published contracts (§6).
2. **Everything optional, degrade gracefully.** The smallest useful instance
   must run with *zero* external accounts: point it at a local git repo, get a
   voiced summary and generative music. Every richer capability (Slack,
   Spotify, SSO, real TTS) is an opt-in plugin that, when absent, the system
   routes around instead of failing.
3. **Plugins over hard-coded integrations.** Data sources, voices, music
   genres, and auth providers are all discovered via entry points, not
   `if/elif` chains. Adding a source = shipping a plugin, not editing core.
4. **Inherit the platform's access model.** State Media FM never invents its own view
   of "who can see what." It collects as a service/bot user and respects the
   source platform's ACLs on every item.
5. **Deterministic, testable core; probabilistic parts at the edges.** LLM
   summarization and TTS are isolated behind provider interfaces with fake
   implementations, so the pipeline is testable without network or API keys.

---

## 2. Architecture overview

```
                         ┌───────────────────────────────┐
                         │        Integration Core        │
                         │  (event bus + scheduler +      │
                         │   tenant/config + orchestrator)│
                         └───────────────────────────────┘
        ingest ▲            │ NewsItem stream      │ broadcast plan
               │            ▼                      ▼
   ┌───────────┴──┐  ┌──────────────┐  ┌──────────────────┐  ┌────────────┐
   │  Sources     │  │  Newsroom    │  │  Generative      │  │  Music     │
   │  (git/jira/  │  │  (summarize  │  │  Music (Strudel  │  │  (Spotify/ │
   │   slack/     │─▶│  → script →  │  │  program gen +   │  │   Apple    │
   │   grafana)   │  │  → TTS voice)│  │  activity model) │  │   mixing)  │
   └──────────────┘  └──────────────┘  └──────────────────┘  └────────────┘
                         │                      │                  │
                         └──────────┬───────────┴──────────────────┘
                                    ▼
                         ┌───────────────────────────────┐
                         │  Web client (Tufte UI +        │
                         │  Strudel player + visualizer)  │
                         │  polls server for stream plan  │
                         └───────────────────────────────┘
        cross-cutting: Auth/SSO · Access control · Packaging/Deploy
```

**Runtime shape:** a Python server (async web API + background scheduler) that
produces a rolling **broadcast plan** — an ordered, timed list of segments
(news read-outs as audio URLs, Strudel music programs as text, song cues). The
web client is a thin player that polls the plan and renders/plays it with
fade-in/out between segments. State is per-tenant.

---

## 3. Tech stack & key decisions

These are the working defaults. Where a decision is genuinely open it's marked
**(assumption — revisit)** and echoed in §10.

| Concern | Choice | Notes |
|---|---|---|
| Backend language | **Python 3.12+** | Implied by "installable by uv." |
| Packaging/deps | **uv** + `pyproject.toml`, single `statemediafm` package w/ plugin entry points | `uvx statemediafm` to run the demo. |
| Web framework | **FastAPI** + `uvicorn` | Async, OpenAPI for free, easy background tasks. |
| Background scheduling | **APScheduler** (in-process) for MVP; pluggable to a queue later | Drives the news cadence + rhythm. |
| Persistence | **SQLite** default (per-tenant file); Postgres via same SQLAlchemy layer | Keeps zero-config promise; scale-up path exists. |
| LLM summarization | **LiteLLM** as the provider layer behind an `LLMClient` interface; a **model config** holds LiteLLM params (model, api_base, keys, temperature, max_tokens). Dev default routes to the **local Claude client** (`anthropic/claude-opus-4-8`). Stubs for other proxies/harnesses. | LiteLLM keeps the pipeline provider-neutral; internal data can later point at a self-hosted model by changing config only. See §5.2. |
| TTS / voicing | **Provider interface**, default = offline engine (e.g. Piper) for demo; cloud voices as plugins | Themed "voice modules" are config on top of a TTS provider. |
| Generative music | **Strudel** (TidalCycles-in-JS) — server generates program *text*, client plays it | No audio rendered server-side; text is the transport. |
| Frontend | **Lightweight** — server-rendered HTML + a small TS bundle for the Strudel player & visualizer | Tufte aesthetic favors restraint; avoid a heavy SPA unless needed. |
| Auth/SSO | **Authlib**-based OIDC/OAuth2 + LDAP | gmail/Entra/MS Graph/Okta as configured providers. |
| Container | **Distroless/slim** image, `uv` install | One process for API+scheduler in MVP; split later. |

---

## 4. Proposed repository layout

```
statemediafm/
├── pyproject.toml            # uv-managed, defines console script + plugin groups
├── PLAN.md                   # this file
├── README.md                 # quickstart / demo
├── src/statemediafm/
│   ├── core/                 # integration core: event bus, orchestrator, scheduler
│   │   ├── models.py         # NewsItem, Segment, BroadcastPlan, ActivitySignal (§6)
│   │   ├── bus.py            # async pub/sub between pillars
│   │   ├── scheduler.py      # rhythm-of-the-day cadence engine
│   │   ├── tenant.py         # per-tenant config + state
│   │   └── plan.py           # assembles BroadcastPlan from pillar outputs
│   ├── sources/              # PILLAR: ingestion
│   │   ├── base.py           # Source plugin ABC + registry
│   │   ├── git_source.py     # MVP source
│   │   ├── slack_source.py
│   │   ├── jira_source.py
│   │   └── grafana_source.py
│   ├── newsroom/             # PILLAR: summarize → script → voice
│   │   ├── summarize.py      # NewsItem[] → prompt → LLMClient → radio script
│   │   ├── llm/              # LLM client abstraction
│   │   │   ├── base.py       #   LLMClient ABC + LLMConfig (model-config schema)
│   │   │   ├── litellm_client.py  # default: wraps litellm.completion(**config)
│   │   │   ├── fake.py       #   deterministic offline client for tests
│   │   │   └── stubs.py      #   other proxies/harnesses (raise NotImplementedError)
│   │   ├── model_config.yaml # LiteLLM params; dev profile = local Claude client
│   │   ├── tts.py            # TTS provider ABC + offline default + adapters
│   │   └── voices/           # themed voice modules (bbc, john-peel, rasta, ...)
│   ├── genmusic/             # PILLAR: generative Strudel music
│   │   ├── activity.py       # NewsItem stream → ActivitySignal (volatility, etc.)
│   │   ├── brainwave.py      # intensity ↔ theta/alpha/beta/gamma mapping
│   │   ├── styles/           # tintinnabuli, lofi, space-dub, bleep, aphex-fugue
│   │   └── compose.py        # ActivitySignal + style → Strudel program text
│   ├── music/                # PILLAR: streaming-service integration (optional)
│   │   ├── base.py           # music provider ABC
│   │   ├── spotify.py
│   │   ├── apple.py
│   │   └── mixer.py          # per-tenant similarity + serendipity cue picker
│   ├── auth/                 # cross-cutting: SSO/OIDC/LDAP + access control
│   ├── web/                  # FastAPI app, routes, templates (Tufte), TS client
│   │   ├── app.py
│   │   ├── templates/
│   │   └── client/           # Strudel player + visualizer (TS, small bundle)
│   └── cli.py                # `statemediafm demo`, `statemediafm serve`, `statemediafm source ...`
└── tests/                    # mirror of src/, one suite per pillar + integration
```

---

## 5. The pillars

Each pillar below lists its **responsibility**, its **contract** (what it
consumes/produces), and an **MVP → full** split.

### 5.1 Sources (ingestion)
- **Responsibility:** connect to a platform as a bot/service user, pull recent
  activity, normalize to `NewsItem`s, attach the source's ACL metadata.
- **Contract:** `Source.poll(since) -> list[NewsItem]`; registered via entry point.
- **MVP:** `git_source` (read a local/remote repo's recent commits, branches,
  PRs if available) + `slack_source` (join a channel, read recent messages).
- **Full:** Jira, Grafana chart parser (render/interpret panels → textual
  observations), generic webhook source; per-source ACL propagation.

### 5.2 Newsroom (summarize → script → voice)
- **Responsibility:** turn a window of `NewsItem`s into a 1–2 minute radio
  **script** (who/what/where/when/why/how), then **voice** it to audio.
- **Contract:** `summarize(items, style) -> Script`; `tts.render(script, voice) -> AudioRef`.
- **MVP:** `summarize()` renders the `NewsItem` window into a prompt and sends it
  through the **`LLMClient`** abstraction, whose default implementation is
  **LiteLLM** (`litellm.completion(**model_config, messages=...)`). A
  deterministic **fake** client backs the tests. Offline TTS (Piper) produces a
  WAV/OGG served by the API.
- **Full:** themed voice modules (BBC world 70s–80s, John Peel, BBC pidgin,
  rastafarian, public/student/alternative radio), each a prompt+voice preset;
  cloud TTS adapters; multi-voice segments (per-participant voices).

#### 5.2.1 LLM integration (LiteLLM)
The summarizer never calls a provider SDK directly — it depends only on:

```
class LLMClient(ABC):
    def complete(self, prompt: str, cfg: LLMConfig) -> str: ...
```

(The newsroom's `summarize()` builds the prompt and calls `client.complete()`;
the client stays a generic, domain-neutral completion primitive.)

- **`LiteLLMClient` (default):** thin wrapper over `litellm.completion(...)`.
  Provider-neutral — the target model is entirely a matter of config, so
  swapping the dev Claude backend for a self-hosted model later is a config
  edit, not a code change.
- **`LLMConfig` / `model_config.yaml`:** the model-selection surface. Holds
  LiteLLM parameters — `model`, optional `api_base`, api-key env-var reference,
  `temperature`, `max_tokens`, `timeout` — as named profiles.
- **Dev profile → the local Claude client.** Default profile targets
  `anthropic/claude-opus-4-8` (current Opus; LiteLLM's `anthropic/` provider),
  authenticating from the `ANTHROPIC_API_KEY` (or `ANTHROPIC_AUTH_TOKEN`)
  environment variable. No key is hard-coded. Note: LiteLLM's Anthropic provider
  reads only these env vars — it does **not** fall back to a local
  `ant auth login` profile, so one of them must be set for `--live`.
- **Stubs for other proxies/harnesses:** `stubs.py` holds placeholder
  `LLMClient` implementations (e.g. a direct-Anthropic-SDK client, an
  OpenAI-compatible proxy client, a local-inference-harness client) that
  satisfy the interface and `raise NotImplementedError` — wiring points for
  later backends without disturbing the pipeline.

Example `model_config.yaml`:

```yaml
default: dev
profiles:
  dev:                              # local Claude client
    model: anthropic/claude-opus-4-8
    # api key from ANTHROPIC_API_KEY (or ANTHROPIC_AUTH_TOKEN) in the env
    temperature: 1                  # Opus 4.8 requires temperature=1
    max_tokens: 1024
    timeout: 60
  self_hosted:                      # example future profile — config only
    model: openai/local-model
    api_base: http://localhost:8000/v1
    api_key_env: LOCAL_LLM_KEY
    max_tokens: 1024
```

### 5.3 Generative music (Strudel)
- **Responsibility:** derive an `ActivitySignal` from the news stream
  (activity level, volatility, #participants, themes) and compose a **Strudel
  program (text)** in a selected style, at an intensity mapped to brainwave
  bands. All pieces fade in/out. Sessions start at **theta**, adapt upward.
- **Contract:** `activity(items) -> ActivitySignal`; `compose(signal, style, intensity) -> StrudelProgram(text)`.
- **MVP:** one style (lofi), signal from message/commit counts, fixed theta
  start with a simple intensity ramp; program text delivered to client.
- **Full:** all styles (tintinnabuli piano/quartet, space-dub, modular bleep,
  aphex fugue-state on close data match), user base-intensity setting,
  brainwave-band adaptation, participant→voice/theme mapping.

### 5.4 Music / streaming integration (optional)
- **Responsibility:** connect users' Spotify/Apple accounts, build a private
  per-tenant similarity model across users' playlists, and cue "familiar"
  songs a few times per hour for serendipity — playback via the user's own
  account/SDK.
- **Contract:** `provider.playlists(user) -> Tracks`; `mixer.next_cue(tenant, now) -> SongCue`.
- **MVP:** stub/free-tier demo cues (no account required) so the rhythm has
  song slots even without integration.
- **Full:** OAuth to Spotify/Apple, cross-user similarity mixing kept private,
  playback handoff to client SDKs.

### 5.5 Scheduler / "rhythm of the day" (in the core)
- **Responsibility:** produce the timed `BroadcastPlan`: news every *n*
  minutes (default **every 15, offset −9 from the hour**), generative music
  between, familiar songs a few times/hour — target a 2–5 minute felt cadence.
- **Contract:** `plan(tenant, window) -> BroadcastPlan` (ordered Segments with start times).

### 5.6 Web client / UI (Tufte)
- **Responsibility:** Edward-Tufte-aesthetic dashboard: streaming-account
  configurator, current song, last-period headlines + summaries, and a
  medium/incidental **visualizer** reflecting voices/activity/intensity.
  Embeds the **Strudel player**, polls the server for the next program, and
  crossfades between poll events.
- **Contract:** consumes `GET /plan` (JSON) + audio/program refs; no business logic client-side.

### 5.7 Auth/SSO & access control (cross-cutting)
- **Responsibility:** OIDC/OAuth2 SSO (gmail, Entra + MS Graph, Okta) and LDAP
  login; enforce that a user only hears items their source-platform identity is
  entitled to.
- **MVP:** single-tenant, local login, ACL metadata carried but trivially
  enforced. **Full:** full provider set + per-item ACL filtering.

### 5.8 Packaging / deploy (cross-cutting)
- **Responsibility:** `uvx statemediafm demo` for zero-config local run; container
  image for hosted multi-tenant deployment; config via file + env.

### 5.9 Commercial modules & licensing (open-core, cross-cutting)
State Media FM is **open-core**. The base station — sources, the deterministic news
copy, the generative music, the browser player — is free, fully offline, and
never needs a key. A **commercial distribution** adds *modules* (the first being
**themed voice personas**, `voice-personas`) that are unlocked by a **license
key**. Implemented in `statemediafm/licensing.py`.

- **Entitlement model.** Each commercial feature registers a stable module
  **slug** via `register_module(slug, name, description)`. The feature's
  enable-points guard on `entitled(slug)` / `require(slug)` (raises
  `LicenseError`). Everything not gated is free.
- **Key resolution.** `$STATEMEDIAFM_LICENSE`, else a gitignored `statemediafm.license`
  file (`$STATEMEDIAFM_LICENSE_FILE` overrides), else open-core only. The key is never
  logged or returned by the API. Web: `GET /license` (status — modules +
  `entitled` flags, never the key), `POST /license` (save key in the body).
- **Offline verification, no phone-home.** A key is a signed token verified
  locally; nothing is sent anywhere. Stdlib only (`hmac`/`hashlib`/`base64`),
  so it works in the zipapp. `["*"]` in a key unlocks everything; `exp` is an
  optional epoch expiry.
- **Enforcement is server-side, at the enable-point** (e.g. `POST /persona`
  returns **402** unlicensed), not merely hidden in the UI. The UI reflects the
  lock (personas shown 🔒, a key field to unlock) but is not the gate.
- **⚠ Scaffold vs. shipped.** The current verifier is an **HMAC-signed token** —
  fine to scaffold the gate and issue dev keys (`sign_license`), but a shipped
  product **must** replace the shared secret with **asymmetric** verification (an
  Ed25519/RSA *public* key baked in; private key held by the vendor) or a signing
  license server, because a shared HMAC secret lives in the verifying binary and
  can be extracted. This swap is isolated to `licensing._secret`/`_verify`.

**When does a new feature need licensing scaffolding?** Decide per feature — most
do **not**. A feature is gated **only if all three hold**: (1) it ships in the
commercial distribution, not the open core; (2) it adds material value a paying
user would expect to pay for (a curated pack, an integration, a scale/ops
capability) — *not* a core primitive, bug-fix, or table-stakes control; and
(3) it can be cleanly disabled, leaving a coherent free experience (personas →
fall back to the free Custom style/voice). If gated: `register_module(slug, …)`
and wrap the enable-points with `require(slug)` — no other change (the license
core already exists). If free (the default — base sources, the offline copy,
genmusic, the scheduler, narration Custom controls): **do nothing**, add no slug.

---

## 6. Integration contracts (the core data model)

These are the shared types every pillar speaks. Get them right early; they are
the project's spine.

- **`NewsItem`** — normalized unit of activity.
  `{ id, tenant, source, kind, actors[], timestamp, title, body, refs[], acl, raw }`
- **`ActivitySignal`** — windowed features for music.
  `{ window, volume, volatility, participant_count, themes[], actor_voices{} }`
- **`Script`** — a voiced-news unit. `{ text, style, voice, segments[] }`
- **`StrudelProgram`** — `{ text, style, intensity, brainwave_band, fade_ms }`
- **`SongCue`** — `{ track_ref, provider, reason }`
- **`Segment`** — one plan entry. `{ start, duration, type: news|music|song, payload_ref }`
- **`BroadcastPlan`** — ordered `Segment[]` for a tenant over a time window.

Rules: pillars communicate **only** via these types over the core event bus /
plan; each type is versioned and has schema tests.

---

## 7. Phased roadmap

Each milestone ends in a **runnable demo**. Ship the walking skeleton before
adding breadth.

### M0 — Skeleton & contracts (foundation)
- `uv` project, `pyproject.toml`, console script, CI (lint+test), pre-commit.
- Define §6 models with schema tests. Stub FastAPI app with `/health`, `/plan`.
- Core event bus + tenant config loader. Fakes for LLM + TTS providers.
- **Demo:** `statemediafm serve` boots; `/plan` returns an empty plan.

### M1 — Vertical slice: git → summary → voice → play  ⭐ *the concept's demo*
- `git_source` (recent commits of a target repo) → `NewsItem`s. ✅
- `forge_source` (GitHub/GitLab issues + merge/pull requests, each with its
  **latest comment**) → `NewsItem`s; `open_source()` routes a forge URL here and
  a local/bare repo to `git_source`. Optional API token; stdlib-only. ✅
- `hackernews_source` (news.ycombinator.com front page via the HN API) →
  `NewsItem`s; `statemediafm demo --hn`. stdlib-only. ✅
- **Multi-source segments / scheduler seed** (`core/schedule.py`): `Cadence` +
  `Programme` + `assemble_broadcast` place each source at its own times as
  titled `Segment`s; `statemediafm broadcast` airs several sources as an interleaved
  rundown. Pure/deterministic (no wall-clock reads). Groundwork for §5.5. ✅
- Newsroom: `summarize()` → prompt → **`LLMClient`** → 1–2 min who/what/… script.
  Ship `LiteLLMClient` (default profile = local Claude client,
  `anthropic/claude-opus-4-8`) + the `fake` client for tests + the proxy/harness
  stubs. `model_config.yaml` drives model selection.
- Offline TTS → audio served by API. Minimal Tufte page that plays it.
- Scheduler produces a plan with a single news segment.
- **Demo:** point at an active git repo → hear a ~90s voiced news read-out.

### M2 — Generative music vertical slice
- ✅ `genmusic/activity.py`: news stream → `ActivitySignal` (volume, volatility,
  participants, themes, actor→voice map). Deterministic.
- ✅ `genmusic/brainwave.py`: intensity ↔ band mapping; theta-start, activity-lift.
- ✅ `genmusic/compose.py` + `styles/lofi.py`: `ActivitySignal` → `StrudelProgram`
  (Strudel source text). Golden/deterministic; `statemediafm genmusic` CLI demo.
- ✅ Program-text endpoint `GET /genmusic` (JSON) on the FastAPI app.
- ✅ `statemediafm serve` (`serve.py`): boots uvicorn + a background refresh loop that
  reuses the roster — each tick recomputes the music program (and re-voices the
  news plan only when activity changes), publishing live `/genmusic` + `/plan`.
  `refresh_once` is web-free and unit-tested.
- ✅ Client-side Strudel player (served at `/`): loads Strudel, plays the polled
  `/genmusic` program after a start-gesture, crossfades via each program's
  `fadeIn` as activity changes, shows live headlines + an incidental
  intensity/brainwave visualizer.
- **Demo:** repo activity audibly modulates continuous generative music. ✅

*M2 complete — server runtime + browser player.*

### M3 — Second source + plugin architecture hardened
- `slack_source` (join channel, summarize). Formalize Source/Voice/Style/Music
  plugin registries via entry points; document "write your own plugin."
- Access-control metadata carried end-to-end (enforced trivially, single-tenant).
- **Demo:** Slack channel → news; adding a source needs no core edits.

### M4 — Rhythm of the day + voices + styles breadth
- Scheduler: news every n-min (default 15, −9 offset), song slots (stubbed),
  music between; 2–5 min felt cadence.
- Themed voice **personas** (start: BBC World, John Peel, Public Radio) — the
  first **commercial module** (`voice-personas`, license-gated; see §5.9). The
  free Custom style/voice controls remain open-core.
- More music styles (space-dub, modular bleep) + full brainwave-band mapping +
  user base-intensity setting.
- **Demo:** ✅ `statemediafm rundown` prints a full "hour of radio" — news
  bulletins on the 17-min cadence, song slots and station idents between, music
  under, with the 2–5 min felt cadence proven. The live player renders the same
  running order from `/schedule`.

### M5 — Music streaming integration (optional pillar)
- Spotify OAuth + playlist read; private cross-user similarity mixer; song cues.
- Free-tier/demo fallback when no account is connected.
- **Demo:** familiar songs surface a few times/hour, privately matched.

### M6 — Multi-tenant, SSO, hardening, packaging
- OIDC (Entra/MS Graph, Okta, Google) + LDAP; per-item ACL enforcement.
- Postgres option; per-tenant isolation; container image; deploy docs.
- Grafana chart parser source; remaining voices/styles; Apple Music adapter.
- **Demo:** hosted multi-tenant instance with SSO login.

*(M4–M6 features are independent enough to reorder by priority.)*

---

## 8. Commercial roadmap — the fork (paid distribution)

> **This is the intended direction.** The open-core line (§7, M0–M6) stays free,
> offline and self-hostable forever. A **commercial distribution** *forks* from it:
> paid **modules** layered on the same pillars and gated by the licensing layer
> (§5.9), with two **marketplaces** and an **enterprise** tier above them. Each
> module registers a slug and guards its enable-points with `require(slug)`;
> nothing here weakens or gates the free core. Sequencing is indicative and some
> specifics (notably the mobile QR bootstrap protocol) are **deferred to
> development time**.

**Prerequisites that must exist first:** licensing (§5.9, *shipped* — but swap to
asymmetric verification before selling), the streaming pillar (§5.4 / M5) for
music modules, SSO/identity (§5.7 / M6) for enterprise modules, and the scheduler
/ Director (§5.5, M4-A *shipped*) for ad slots.

### 8.1 Content & voice modules
- **`voice-personas` — Themed voice personas.** *Shipped (M4-C).* Curated on-air
  identities (BBC World, John Peel, Public Radio) = writing-style + voice +
  station-phrasing bundles. Free tier = the Custom style/voice controls.
- **`premium-voices` — Commercially licensed voices.** High-quality / licensed TTS
  voices beyond the free Piper set, slotting into the `TTSProvider` abstraction
  (§5.2). Likely per-voice sub-entitlements. *Legal:* per-voice / vendor voice
  licensing and consent.

### 8.2 Music streaming modules (extend §5.4 / M5)
- **`apple-music` — Apple Music integration.** Fills song slots (`SongCue`) via
  MusicKit; user-authorised playback. Requires the M5 streaming pillar.
- *(Spotify desktop/base playback is M5 itself; the mobile surface is `ios-spotify`
  in §8.4.)*

### 8.3 Enterprise & identity modules (extend §5.7 / M6)
- **`entra-authz` — Microsoft Entra ID federated authorization to news sources.**
  Delegated / on-behalf-of per-user tokens to sources (GitHub Enterprise, Jira,
  internal APIs) via Entra OIDC, honouring source-side scopes and per-user ACLs
  (§5.7). Enterprise SSO for the station itself.
- **`enterprise-ad-free` — Ad-free mode for enterprises.** An entitlement that
  suppresses the ad marketplace (§8.5) station-wide — the inverse gate: present =
  no ads aired.

### 8.4 Mobile companion apps
- **`ios-app` — iPhone app.** Acquires the user's news-site config + source tokens
  from a running station via a **QR code**, using a **security protocol the user
  will specify at development time** (⚠ deferred). Constraint: source tokens are
  sensitive — the handoff must never expose long-lived secrets; assume ephemeral,
  scoped, channel-bound, one-time, with on-device confirmation until the protocol
  is fixed.
- **`ios-spotify` — Spotify in the iPhone app.** Mobile song-slot playback /
  account link inside `ios-app`.

### 8.5 Marketplaces (two-sided platforms)
- **`avatar-marketplace` — Announcer persona avatar marketplace.** Creators
  publish/sell announcer **avatars** (visual + voice + persona bundle); revenue
  share. Builds on `voice-personas` (+ `premium-voices` for licensed voices).
- **`ad-marketplace` — Advertiser marketplace.** Advertisers buy **targeted ads of
  a fixed length**, delivered as **text the news announcer reads** in dedicated ad
  slots. Needs: ad-slot cues in the Director (§5.5 — an ad cue alongside news/song,
  respecting the felt cadence), targeting + billing + payouts, brand-safety review,
  and the read voiced in the active persona. `enterprise-ad-free` (§8.3) opts an
  org out. *Legal:* sponsorship-disclosure / advertising-standards obligations.

### 8.6 Business-model & risk notes
- **Licensing:** all gates use §5.9; **must** move HMAC → asymmetric (or a signing
  license server) before any paid release.
- **Marketplaces** are two-sided: billing, payouts, creator/advertiser onboarding,
  and content review are net-new subsystems, not just modules.
- **Provider ToS / legal:** Apple Music, Spotify, voice-vendor, Entra, and
  ad-standards compliance each need a review before their module ships (echoes §9).
- **Trust boundary:** the mobile QR handoff and Entra delegation both move real
  credentials — treat as security-critical; the QR protocol spec is a blocking
  prerequisite for `ios-app`.

---

## 9. Testing & quality

- **Per-pillar unit tests** against contracts, using fakes for LLM/TTS/music so
  the suite runs offline and deterministically.
- **Contract/schema tests** for every §6 type; version bumps require a test.
- **Integration test** for the M1 slice (git → plan) as the always-green
  smoke test.
- **Golden-file tests** for generated Strudel program text per style.
- Lint/format/type-check (ruff + mypy) in CI; pre-commit hooks.

---

## 10. Open questions (resolve before the milestone that needs them)

1. ~~**LLM provider (M1):**~~ **Resolved** — LiteLLM behind an `LLMClient`
   interface, model selection via `model_config.yaml`; dev default routes to the
   local Claude client (`anthropic/claude-opus-4-8`); stubs for other
   proxies/harnesses. Provider-neutral, so a self-hosted model is a later
   config-only change. See §5.2.1.
2. **TTS engine (M1):** confirm offline default (Piper?) and which themed
   voices need cloud voices to sound right.
3. **Frontend weight (M2):** server-rendered + small TS bundle vs a full SPA —
   depends on how interactive the configurator/visualizer must be.
4. **Grafana parsing (M6):** parse rendered panel images vs query the Grafana
   API/datasource directly? The latter is more robust if available.
5. **Multi-tenancy timing:** build single-tenant through M4 and retrofit, or
   bake tenant isolation in from M0? (Plan assumes carry the field early,
   enforce late.)
6. **Legal/ToS:** Spotify/Apple playback + cross-user playlist comparison must
   respect each provider's ToS and user privacy — needs a check before M5.
```
