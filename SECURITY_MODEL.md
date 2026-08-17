# State Media FM — Security Model

This document describes the **trust model, assumptions, and responsibility split** for
State Media FM, so that someone reading the code before running it understands what
the app protects, what it delegates, and what the operator must do. It is a
description of intent and current state — not a claim of formal audit. Concrete
findings and the hardening backlog live in [`HARDENING_PLAN.md`](./HARDENING_PLAN.md).

## 1. What this app is (posture)

State Media FM is a **self-hostable, single-operator** application. In its default
posture it:

- **Binds to loopback** (`127.0.0.1`) — the control API and player are meant for the
  person running it, on their own machine.
- **Is offline / has no phone-home.** There is no telemetry, analytics, or vendor
  callback. Licensing is verified locally (currently stubbed — see §7). Every network
  request the app makes is a **functional, operator-configured** call: to the news
  sources you add, the LLM gateway you point it at, Spotify (only if you connect it),
  a one-time Piper voice download, and two CDN `<script>` loads on the player page.
- **Keeps the core free and dependency-light**, so it can ship as a single-file
  zipapp and be read end-to-end.

## 2. Responsibility split (the core assumption)

State Media FM is a **thin aggregator of activity from external services**. Its
security model is a division of responsibility:

| Concern | Owned by |
|---|---|
| Correctness of **this app's own code** (no injection into the browser, safe rendering, no secret leakage in git) | **State Media FM** |
| **Authorization, access scoping, rate limiting, and audit** of the activity it reads | **The edge services** (GitHub, GitLab, Jira, Slack, PagerDuty) via the tokens you grant |
| **Content safety / prompt-injection defenses** of the model that writes the news | **The LLM gateway and model** you configure |
| **Scoping the tokens** you paste to least privilege; **not exposing** the app to untrusted networks | **The operator** |

In other words: **the app secures its own code; it relies on the security controls
of the edge services it connects to** for what data it may read and what a token may
do. It does not attempt to re-implement the providers' access control.

## 3. Assumptions the operator must uphold

The model is only sound if these hold:

1. **News-source API tokens are scoped appropriately** — read-only, least-privilege,
   limited to the repos/projects/channels you actually air. The app only ever *reads*
   activity; it never needs write or admin. The Auth panel documents the minimum scope
   per provider. If a token is over-scoped, a leak is as damaging as the token allows —
   the app cannot reduce a token's privilege after the fact.
2. **The LLM gateway and model are trusted to apply their own prompt-injection
   mitigations.** Source content (issue titles, chat messages) is *untrusted input*
   that flows into the news-writing prompt. The app caps/delimits it, but the primary
   defense against prompt injection is the gateway/model's own guardrails. Critically,
   **the app never executes model output** — it is spoken (TTS) and displayed as text,
   never run as code.
3. **The control API is loopback-first.** It binds to `127.0.0.1` by default and is
   protected by a **per-session token plus a Host/Origin allowlist** (§6). A
   non-loopback bind is **refused** unless you explicitly opt in
   (`STATEMEDIAFM_ALLOW_NONLOOPBACK=1`), and auth stays enforced even then. To reach
   it from another machine (browse via the server's hostname/IP), add those names to
   the allowlist with `STATEMEDIAFM_ALLOWED_HOSTS=host1,host2` — otherwise the
   Host/Origin check returns 403. Prefer not to expose it to untrusted networks
   regardless.

## 4. Secret & token storage

- **Where they live now:** news-source tokens/endpoints, the Spotify Client
  ID/Secret, and the Spotify OAuth refresh token are stored **locally** in
  `statemediafm.auth.toml`, written **owner-only (`chmod 0600`)** and **gitignored**
  (`*.auth.toml`) — verified never committed. A license key, if set, is stored in
  `statemediafm.license` (also `0600`, gitignored). These files are **plaintext**;
  treat `statemediafm.auth.toml` as **account-equivalent** — do not sync, back up to
  shared storage, or commit it. Tokens are **masked** in the UI (only a `…last4`
  hint is ever returned) and are never logged.
- **What the app does with them:** attaches them to the outbound read requests to the
  provider you configured; nothing else.
- **Planned:** pluggable **external secret stores** — integrations with
  **Bitwarden, HashiCorp Vault, 1Password**, and similar — so tokens can be fetched
  from a managed vault at runtime instead of the local plaintext file. This is a
  future iteration; the storage boundary (a single resolver that reads the token for a
  source) is already isolated to make that swap straightforward.

## 5. Prompt-injection stance

Source content is untrusted. The app's position:

- **The model output is never executed** — only voiced and shown as text. So a
  successful prompt injection can, at worst, make the *news copy* say something the
  attacker wants; it cannot run code, read secrets, or pivot.
- The app **delimits and length-caps** source content before it reaches the prompt,
  and defaults to a **deterministic, offline copy** path when no LLM is configured.
- Beyond that, the app **relies on the gateway/model's own injection mitigations**.
  Choose a gateway/model you trust, and scope its key (and any spend cap) accordingly.

## 6. What the app mitigates in its own code

Verified controls (kept under test so they can't silently regress):

- **No stored-XSS in the player:** every browser sink that renders external data
  (source titles/URLs, Spotify track/playlist names) is HTML-escaped or uses
  `textContent`.
- **No injection into the generated music:** source text is reduced to a strict-charset
  token list and consumed only as a numeric hash; no attacker-controlled string reaches
  the client-side–evaluated Strudel program. The emitter is constrained to a verified
  primitive whitelist.
- **No secrets in version control:** `.gitignore` covers `*.auth.toml`, `*.license`,
  `voices/`, model/audio artifacts; the tree is verified clean.
- **Control-API authentication:** every route except a small public set (the page,
  `/health`, audio clips, the Spotify OAuth redirects) requires a **per-session
  token** embedded in the served page. A cross-origin page cannot read that page's
  body, so it cannot learn the token; the custom `X-SMFM-Token` header also forces a
  CORS preflight cross-origin, which is denied.
- **Host/Origin allowlist:** requests whose `Host` (DNS-rebinding defense) or
  `Origin` (cross-site defense) is not a known loopback/bind host are rejected. A
  non-loopback bind is refused without an explicit opt-in.
- **Least-surprise networking:** offline by default, no telemetry, loopback bind.

## 7. Known limitations (operator responsibilities)

Be honest with a read-before-run reader — these are **not yet mitigated in code** and
are tracked in [`HARDENING_PLAN.md`](./HARDENING_PLAN.md):

- **Outbound-request (SSRF) trust:** endpoints you configure (LLM gateway, Jira/Slack/
  PagerDuty, repo URLs) are fetched by the server without private-range blocking. Only
  point them at hosts you trust. *(Tracked as the next hardening item.)*
- **No CSP / SRI** on the two CDN scripts yet — a compromised CDN could serve altered
  Strudel/Spotify-SDK JS into the page. (Control-API auth and Host/Origin
  CSRF/DNS-rebinding protections **are** now in place — see §6.)
- **Licensing verification is stubbed** (the forgeable HMAC scaffold was removed). No
  license key unlocks anything until an **asymmetric** verifier (a baked-in public key,
  vendor-signed keys) is implemented; until then the open-core base is fully free and
  commercial modules stay locked — the safe default.

## 8. Reporting

This is pre-release software intended for self-hosting. If you find a security issue,
open an issue (or contact the maintainer) with a description and reproduction; do not
post exploit details for anything that could affect other self-hosters until a fix is
available.
