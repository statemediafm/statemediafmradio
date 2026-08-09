# StateMediaFM — Hardening, Cleanup & Sharing-Readiness Plan

_Prepared from a two-track analysis (security review + code-hygiene/architecture/readiness audit) of the `statemediafm` package. Every item cites `file:line`. This is a **plan**, not applied changes._

## TL;DR

The code is in better shape than most pre-release projects: **no secrets are tracked in git**, the auth file is `0600`-gitignored, there is **no telemetry / phone-home**, XSS sinks are consistently `esc()`-escaped, and the Strudel emitter cannot be injected with source data. The real gaps are three:

1. **The local control API has zero authentication** — and it can write secrets, hand out a live Spotify token, and make server-side outbound requests. This is the root of most security findings.
2. **The web ↔ serve layers are cross-coupled** and the web layer holds business logic, drifting from the pillar architecture (PLAN §1/§2/§5/§6).
3. **The repo isn't dressed for a read-before-run audience** — no `LICENSE` file, stale README with no trust story, a committed prebuilt binary, dead endpoints, no CI.

Order of work is at the bottom (§5).

---

## 1. Mitigating vulnerabilities

Severity reflects real-world impact for a localhost tool that a user might expose or run on a shared box.

### CRITICAL

- **C1 — No auth/authz on the entire control API.** `web/app.py` `create_app` (all routes); bindable to `0.0.0.0` via `serve.py:313,418`, `cli.py:552`. Any reachable client can read masked auth (`GET /auth`), **write** tokens/endpoints (`POST /auth`, `POST /spotify`), obtain a live Spotify token (`GET /spotify/token`), trigger server fetches (`POST /sources`), and save a license key (`POST /license`).
  - **Fix:** mandatory per-install bearer token printed at startup, required on all mutating + secret-reading routes (a FastAPI dependency); **refuse to bind non-loopback without it**; document that `--host 0.0.0.0` unauthenticated is unsafe.

- **C2 — `/spotify/token` returns a live, broadly-scoped user token to any client.** `web/app.py:716‑724`; scopes `spotify.py:29‑33` (`streaming`, playback control, private playlists, email/identity). Browser-exposed by design (the SDK + inline JS at `web/app.py:1354‑1357,1377` call `api.spotify.com` directly).
  - **Fix:** auth-gate it (C1) + strict same-origin/Host check; **minimize scopes** (drop `user-read-email`/`user-read-private` if unused); consider **proxying** Spotify Web API calls server-side so the token never reaches the browser.

### HIGH

- **H1 — SSRF with credentials attached.** User-set endpoints (`POST /auth` `web/app.py:549‑564`) are fetched server-side by `litellm_client.py:34‑66` (`/news-model/discover`), `sources/jira.py:50‑72`, `slack.py:61‑69`, `pagerduty.py:50‑62`, and `POST /sources` → non-forge URL falls through to **`git clone` of an arbitrary remote** (`git_source.py:46‑58`). No scheme/host/redirect restrictions; stored tokens are sent in the request.
  - **Exploit:** point `llm-gateway` at `http://169.254.169.254/…` (cloud metadata) or `http://127.0.0.1:6379/`, then `POST /news-model/discover`; or point `jira` at a collector to exfiltrate the stored `email:api_token`.
  - **Fix:** on every outbound fetch, allow only `https`, resolve host and reject RFC1918 / loopback / link-local / `169.254.169.254` / IPv6 ULA, re-check after redirects (or disable redirects); per-source host allowlists where feasible; never `git clone` an unvalidated remote.

- **H2 — CSRF / DNS-rebinding.** No CORS config, no `Host`/`Origin` validation; many mutating routes are simple query-param POSTs (`/broadcast`, `/model`, `/demo`, `/news-model/discover`, …). A visited web page can drive side-effecting routes (`fetch(..., {mode:'no-cors'})`); a DNS-rebinding page becomes same-origin and can **read** `/auth`, `/spotify/token`, `/spotify/me`.
  - **Fix:** validate `Host` against a loopback allowlist (defeats rebinding); require the C1 token / a custom header on state-changing routes; move query-param POSTs to JSON bodies (forces preflight).

- **H3 — License gate trivially bypassable (commercial-integrity, not host-compromise).** `licensing.py:96‑99` uses **symmetric HMAC** with a hardcoded default secret (`"STATEMEDIAFM-DEV-SECRET-CHANGE-ME"`) bundled into `dist/statemediafm.pyz`. Anyone can `sign_license(["*"])` and `POST /license` to unlock all modules. Already flagged in-code as a scaffold.
  - **Fix (before selling):** switch to **asymmetric** verification — bake an Ed25519/RSA **public** key into the binary, sign keys with a vendor-held private key, ship no secret; production builds fail closed if no public key is configured. Keep the dev secret behind an env var for tests only.

### MEDIUM

- **M1 — No CSP / security headers; third-party JS without SRI.** `web/app.py:1039` (`@strudel/web@1.0.3`, pinned but no SRI), `:1791` (`sdk.scdn.co/spotify-player.js`, unpinned), `:1426` (dirt-samples). Only `Cache-Control` is set. A CDN compromise yields JS in a context holding the Spotify token.
  - **Fix:** add a CSP (`script-src`/`connect-src`/`frame-src` limited to the exact origins), SRI hashes (or **vendor the libs locally** into the zipapp — also improves the offline story), and `X-Content-Type-Options`/`X-Frame-Options`/`Referrer-Policy`.

- **M2 — Long-lived Spotify refresh token + client secret persisted in plaintext.** `web/app.py:701‑704` + `auth.py:62‑84`. Storage hygiene is good (`0600`, gitignored, untracked) but a refresh token ≈ permanent account access; the `chmod` failure is silently swallowed (`auth.py:82‑84`).
  - **Fix:** warn if `0600` can't be verified; document the file is account-equivalent (never sync/backup/commit); consider OS keychain; keep the easy full-revoke (logout already clears it, `web/app.py:739‑747`).

- **M3 — OAuth `state` is a single server-wide value.** `web/app.py:66,679,691‑693`. Good entropy, one-time use, but not per-session (weak under concurrent logins).
  - **Fix:** bind `state` to a per-client session once C1 auth exists; add a short TTL.

### LOW / already-good (state explicitly so a future change can't regress it)

- **L1 — XSS: no live issue found.** Every external-data `innerHTML` sink applies `esc()` (`web/app.py:1239‑1244,1263‑1269,1340‑1341,1508,1556‑1558,1609‑1611,1720‑1724`); playlist names use `textContent`. **Keep a regression test asserting `esc()` on these paths.**
- **L2 — Strudel injection: closed by design.** The emitter (`genmusic/ir.py:120‑150`) interpolates only style literals; source titles become a strict-regex `themes` list (`activity.py:61‑70`) consumed as a **numeric hash** only. **Add an emitter-side assertion** that any interpolated field matches a safe charset, so a future style can't reintroduce it.
- **L3 — Prompt injection into the news LLM (low stakes).** Untrusted source content flows into the model (`serve.py:101‑118`). Output is spoken/displayed, not executed. **Fix:** wrap source content in "untrusted data" delimiters; length already capped.
- **L4 — Minor:** `/audio/{clip_id}` unauth (low sensitivity, reachable per C1); ensure tokens are never logged in the broad `except` blocks (`web/app.py:643‑644,665‑666`).

### "Before sharing publicly" security checklist

1. Add auth to the control API (C1); refuse non-loopback without it.
2. Validate `Host`, lock down CORS, JSON-body the POSTs (H2).
3. Auth-gate + minimize scopes on `/spotify/token`; prefer server-side proxy (C2).
4. Block SSRF on every outbound fetch; don't clone unvalidated remotes (H1).
5. Asymmetric license verification before charging (H3).
6. CSP + SRI + headers; ideally vendor the CDN JS (M1).
7. Warn on unverifiable `0600`; document the auth file is account-equivalent (M2).
8. Lock the `esc()` and numeric-only-genmusic invariants under test (L1, L2).

---

## 2. Pruning unused code

Confirmed dead (definition + tests, but no runtime/UI caller):

| Item | Location | Note |
|---|---|---|
| `GET`/`POST /style` | `web/app.py:338‑350` | style field removed from UI; only `tests/web/test_app.py:179‑182` call it |
| `_STYLE_SUGGESTIONS` | `web/app.py:336` | used only by dead `/style` |
| `GET /schedule` | `web/app.py:125‑148` | running-order panel removed from player; only `test_app.py:285‑288` |
| `_State.session_start` | `web/app.py:90`, set `serve.py:390` | read only by dead `/schedule` |

- **Action:** delete the above + their tests; **update PLAN §7 M4** (its "player renders the running order from `/schedule`" claim is now false) — or, if the panel should return, file that as a feature instead.
- **Stray artifact:** delete `maelcom-demo.wav` on disk (gitignored, untracked, but present).
- **Keep (intentional / verified live):** `newsroom/llm/stubs.py` (deliberate unwired wiring points per PLAN §5.2.1 — add a one-line note), `GET /health`, `styles/scratchpad.py` ("ScratchPad" generator), `core/people.py`, `genmusic/arrange.py`, `songs.py`, `Director`.
- **Clean bill:** no `TODO/FIXME/HACK/XXX` in `src/`, no stray debug prints, ruff passes. The "Maelcom" rename is complete (no residual refs in code/docs; only the folder path `/Maelcom` remains, intentional).

---

## 3. Optimizing flows to conform to the original architecture (PLAN §1/§2/§5/§6)

The core pillars (sources → newsroom → genmusic → core contracts) are clean and contract-driven. The drift is concentrated at the **web/serve boundary**.

- **B1 — Bidirectional web ↔ serve coupling (HIGH).** `web/app.py` imports serve internals `DEMO_REPO` (`:193`), `publish_song` (`:282`); `serve.py:350` imports web internals `_State`, `create_app`. Violates PLAN §1.1 ("no pillar imports another pillar's internals — only published contracts") both ways.
- **B2 — `_State` is a fat session/business object in the web layer (HIGH).** `web/app.py:47‑98` holds the whole app session (Spotify OAuth tokens `:61‑67`, song index, live roster, director, mix settings, news-model overrides), not just "latest plan + audio" (its §5.6 job) — yet it's driven by `serve.run`.
- **B3 — Business logic in the web layer (HIGH).** Contradicts §5.6 and the file's own docstring. Spotify OAuth/token lifecycle (`_sp_restore`/`_sp_valid_token` `:621‑667`, code exchange + refresh persist `:682‑707`) is music-pillar logic; roster mutation lives in `POST /demo` (`:185‑225`); `publish_song` is called from `POST /mix` (`:282‑284`).
- **B4 — Inline HTML/JS mega-string (MEDIUM).** `_PLAYER_HTML` is ~1015 of `web/app.py`'s 1793 lines (`:778‑1792`) — HTML + CSS + ~750 lines of un-lintable JS. The single biggest reviewability liability.
- **B5 — Smaller drift.** `_DEMO_DIRECTOR` module-global mutable cache (`serve.py:34,91‑98`); layout diverges from PLAN §4 (no `music/`, `auth/`, `voices/` packages) — reasonable simplifications, so **update PLAN §4 to match reality** rather than force the code.

**Realignment (one focused refactor):**
1. Introduce a **core-owned `SessionState`** (move `_State` out of `web/`); `serve` passes it into `create_app` → `serve` no longer imports a web class, `web` no longer imports `..serve`.
2. Move **Spotify OAuth/session + `publish_song`** into a `music/` pillar (aligns §5.4/§4); web endpoints become thin adapters.
3. Move **`POST /demo`'s roster mutation** behind a core/scheduler helper.
4. Extract `_PLAYER_HTML` → `web/static/player.html` + `player.js`, served via `importlib.resources` (keeps zipapp/stdlib compat; enables JS linting).

---

## 4. Readiness to be shared (read-before-run audience)

Strong trust story that the docs don't yet tell: **offline, no telemetry, secrets `0600`/gitignored, loopback-only default.** Gaps:

- **C1 — No `LICENSE` file (HIGH).** `pyproject.toml:11` declares Apache-2.0 but there's no `LICENSE`. Add it; also state the open-core boundary (PLAN §7/§8) so readers understand the `licensing.py` gate.
- **C2 — README stale + no trust section (HIGH).** README:8 still says "M1 vertical slice" (reality ≈ M4/M5); the Layout block omits `serve.py`, `spotify.py`, `roster.py`, `licensing.py`, `auth.py`, `core/director.py`, `core/schedule.py`, `personas.py`. Add a **"Security & trust"** section: no-auth loopback control API, offline/no-telemetry, where secrets live on disk, and the two runtime CDN script loads (`@strudel` pinned, Spotify SDK unpinned, only in Playlist mode).
- **C3 — Tracked prebuilt binary (MEDIUM).** `dist/statemediafm.pyz` (257 KB) is committed — an opaque, un-diffable artifact in a repo people audit. Untrack it, gitignore `dist/`, build in CI/releases (`scripts/build_standalone.sh` reproduces it).
- **C4 — Contributor ergonomics (MEDIUM).** PLAN M0 promised CI + pre-commit; none exist (no `.github/`, `.pre-commit-config.yaml`, `CONTRIBUTING.md`). README documents only `pytest`, not `ruff`/`mypy`. Add a minimal GitHub Actions ruff+mypy+pytest workflow and a dev section.
- **C5 — Scratch file `concept` tracked (LOW).** Superseded by PLAN.md; move design docs (`LLM.md`/`SOURCES.md`/`ENTRAINMENT.md`/`concept`) under `docs/` or delete `concept`.
- **Positives to preserve:** `.gitignore` secret coverage is thorough and verified (no secret files tracked); licensing never returns/logs the key.

---

## 5. Prioritized, ordered execution plan

**Do first — public-repo blockers (HIGH value / LOW effort):**
1. Add `LICENSE` (Apache-2.0). _(§4 C1)_
2. Rewrite README scope + add "Security & trust" section (loopback + no-auth API, offline/no-telemetry, secrets-on-disk, CDN loads); fix the "M1 slice" line + Layout. _(§4 C2)_
3. Delete dead `/style` + `/schedule` + `_STYLE_SUGGESTIONS` + `session_start` + their tests; correct PLAN §7 M4. Delete `maelcom-demo.wav`. _(§2)_

**Security must-dos before any non-loopback / shared use (HIGH):**
4. Add control-API auth + refuse non-loopback without it. _(§1 C1)_
5. Validate `Host`, lock CORS, JSON-body POSTs. _(§1 H2)_
6. Auth-gate + scope-minimize `/spotify/token`. _(§1 C2)_
7. SSRF allowlisting on all outbound fetches; no unvalidated `git clone`. _(§1 H1)_

**Structural (HIGH value / MEDIUM effort):**
8. Untrack `dist/*.pyz`; gitignore `dist/`; build in CI. _(§4 C3)_
9. Add CI (ruff + mypy + pytest) + README dev section. _(§4 C4)_
10. Break the web↔serve cycle: core-owned `SessionState`, move Spotify/`publish_song` to a `music/` pillar, `POST /demo` roster mutation behind a core helper. _(§3 B1‑B3)_

**When convenient (MEDIUM/LOW):**
11. Extract `_PLAYER_HTML` → `web/static/*`. _(§3 B4)_
12. CSP + SRI + security headers; vendor CDN JS. _(§1 M1)_
13. Asymmetric license verification (before charging). _(§1 H3)_
14. Update PLAN §4 to match the real layout; move `concept`/design docs to `docs/`; state-held `_DEMO_DIRECTOR`; emitter safe-charset assertion + `esc()` regression test. _(§2/§3/§1 L1‑L2)_
