# State Media FM — technical guide

> New here? Start with the [README](README.md) for a plain-English overview and the
> two-command quick start. This file is the full reference: install options, the CLI,
> the zipapp, generative music, the API, and the layout.

Internal streaming radio built on a team's collaboration, project, and
version-control data. It turns activity (git, Slack, Jira, Grafana, …) into a
voiced news broadcast, with generative music that tracks project activity.

See [PLAN.md](PLAN.md) for the architecture and roadmap, and
[SECURITY_MODEL.md](SECURITY_MODEL.md) for the trust model. Milestones **M0–M3**
are in place and **M4** is partly shipped: a live station that turns a team's
recent activity (GitHub/GitLab issues and merge/pull requests with their latest
comments, a local repo's commits, or the Hacker News front page) into a
summarized, **voiced news broadcast**, with **generative
[Strudel](https://strudel.cc) music** that tracks project activity, a **browser
player**, and **optional Spotify** playback between bulletins.

## Quick start

### Standalone, zero dependencies

The offline demo path uses only the Python standard library, so it ships as a
single-file zipapp — no install, no `pip`, no `PYTHONPATH`:

```sh
./scripts/build_standalone.sh
python3 dist/statemediafm.pyz demo --repo /path/to/a/git/repo
```

Choose a source:

- **`--repo <GitHub/GitLab URL>`** (e.g. `https://github.com/meltano/meltano`) —
  reads the most recently updated **issues and merge/pull requests, with the
  latest comment on each** (public projects work unauthenticated, subject to the
  platform's rate limits; pass `--token` or set `GITHUB_TOKEN` / `GITLAB_TOKEN`
  to raise them);
- **`--repo <local/bare repo path>`** — falls back to recent **commits** (all
  that is available without a forge API);
- **`--hn`** — the **Hacker News front page** (top stories via the official HN
  API). Try it with `python3 dist/statemediafm.pyz demo --hn`.

It writes a radio script to stdout and saves the voiced audio to
`statemediafm-demo.wav`. Copy `dist/statemediafm.pyz` anywhere and run it with just
`python3`. Passing **both** `--hn` and `--repo` combines them into one segment,
covering each source in full before the next (depth-first, not interleaved),
with each source's headlines attributed and voiced in its own voice.

The zero-dependency zipapp builds a deterministic summary from the real activity
(top contributors + recent headlines) and voices it with a placeholder tone — so
it needs no credentials or model, yet the output reflects the project.

### Real spoken audio

An installed instance **speaks by default** (offline neural TTS via
[Piper](https://github.com/OHF-Voice/piper1-gpl)); the tone is only a fallback
for the zipapp, which has no `[tts]` extra. Install it and run normally:

```sh
uv pip install -e ".[tts]"          # or: pip install -e ".[tts]"
statemediafm demo --repo /path/to/repo --out news.wav   # spoken; add --tone to force the placeholder
```

The CLI is installed under two names: **`statemediafm`** (canonical) and the
short alias **`smfm`** — use whichever you prefer (`smfm serve`, `smfm demo …`).
The package, environment variables (`STATEMEDIAFM_*`) and config files keep the
full name.

The first run downloads a small voice model (~60 MB) into `./voices/`
(override with `STATEMEDIAFM_VOICES_DIR`); later runs are offline. Pick a voice with
`--voice`:

| Alias | Voice |
|---|---|
| `alan` (default) | British male |
| `alba` | British (Scottish) female |
| `northern_english_male` | Northern English male |
| `southern_english_female` | Southern English female |

You can also pass a full Piper name (e.g. `en_US-lessac-medium`) or a path to
your own `.onnx` model. Speech needs a normal install with the `[tts]` extra;
the zero-dependency zipapp always uses the tone.

### Installed (real models + web)

```sh
uv pip install -e ".[all]"          # or: pip install -e ".[all]"

# Real summaries via the local Claude client (LiteLLM → anthropic/claude-opus-4-8).
# Auth: set ANTHROPIC_API_KEY (or ANTHROPIC_AUTH_TOKEN) in the environment.
statemediafm demo --repo /path/to/repo --live
```

Extras: `.[llm]` (LiteLLM + config), `.[tts]` (Piper neural speech), `.[web]`
(FastAPI + uvicorn), `.[dev]` (pytest, ruff, mypy), `.[all]`.

## Multiple sources as timed segments

`broadcast` airs several sources at **different times**, each on its own cadence,
so they read as distinct news segments about their topic (a step toward the
"rhythm of the day" scheduler):

```sh
# Ad hoc: sources on a shared interval, auto-staggered to interleave.
statemediafm broadcast --hn --repo https://github.com/meltano/meltano --every 15m --window 60
```

It prints a rundown plus the script for each segment:

```
Broadcast rundown — next 60 min, 8 segments:
  16:36  Repository activity        18s
  16:42  Hacker News front page     31s
  16:51  Repository activity        18s
  ...
```

**Configurable roster.** For full control — which sources air, how often, and
staggered by what offset — pass a TOML/JSON roster (see
[`examples/roster.toml`](examples/roster.toml)):

```sh
statemediafm broadcast --config examples/roster.toml --window 60
```

```toml
[[segments]]
topic = "Hacker News front page"
source = "hackernews"
every = "15m"
offset = "6m"

[[segments]]
topic = "Engineering issues"
source = "repo"
repo = "https://github.com/meltano/meltano"
every = "15m"
offset = "0"
```

**Audio.** `--out` (default `news.wav`, `''` to skip) writes one combined WAV of
all segments back to back; `--out-dir DIR` writes one WAV per segment topic. So
`statemediafm broadcast --hn --window 120` alone drops a spoken `news.wav` in the
current directory (installed instances speak by default; `--tone` forces the
placeholder). Each segment is voiced in a **different** rotating voice so the
topics sound distinct. The broadcast opens with a spoken time greeting ("Good day. It is
16:52."), headlines are **attributed** to their source (Hacker News, the git
project) and spaced by `--headline-pause` seconds (default `1.0`).

Durations accept units (`15m`, `90s`, `1h`) or bare seconds. The scheduler
(`core/schedule.py`: `Cadence`, `Programme`, `assemble_broadcast`) is
pure/deterministic — it never reads the wall clock.

## Run the station (zero-config)

Install the full app and run it with **no flags** — it opens the player in your
browser, and everything else is configured in the **Settings** tab:

```sh
uv pip install -e ".[all]"      # or: pip install -e ".[all]"
statemediafm                    # → opens http://127.0.0.1:8150
```

`statemediafm` with no subcommand runs the live server: a background loop keeps the
generative music (`/genmusic`) and voiced news (`/plan`) fresh from activity, and the
browser tab opens automatically (suppress with `--no-open`). Press **▶ Start** to
begin audio — browsers block sound until a user gesture.

Everything is set in **Settings**, and your choices **persist** across restarts
(written to `statemediafm.config.toml`; secrets go to the gitignored
`statemediafm.auth.toml` — see [Security & trust](#security--trust)):

- **Config** — connect your infrastructure: a **self-hosted GitLab** instance URL + a
  read-only PAT, and an **LLM gateway** URL + API key.
- **News Update Sources** — which activity airs (Hacker News is on by default; add
  GitHub/GitLab projects and more).
- **News-parsing model** — a **Live news (LLM)** switch (turn LLM writing on/off with
  no restart) and the model to use (↻ Discover finds what your gateway serves).
  Off → a deterministic offline copy, so news always airs.
- **Cadence** — how often a bulletin airs, and how often sources are polled.
- **Mix** — rotate ambient generators and/or mix in Spotify songs.

Each tick recomputes the Strudel program from current activity and re-voices the news
only when the item set changes (so TTS isn't run every tick). Flags still work as
first-run seeds / overrides — an explicit flag beats the persisted setting:

```sh
statemediafm serve --hn --repo <URL> --live --port 8150   # or: --config examples/roster.toml
```

Needs the `[web]` extra (bundled in `[all]`); `[llm]` for live LLM news, `[tts]` for
spoken audio (otherwise a placeholder tone). To preview the **rhythm of the day**
without serving, `statemediafm rundown` prints a full "hour of radio" — bulletins on
the 17-minute cadence, song slots and idents between, music underneath;
`--news-every` and `--window` tune it.

## Generative music (M2)

State Media FM also turns a repo's activity into a **Strudel program** — generative
music that tracks the project. Activity becomes an `ActivitySignal` (volume,
volatility, participants, themes); that maps to an intensity on the
brainwave-band scale (sessions start at **theta** and rise toward gamma as
activity climbs), and a style renderer emits Strudel source text:

```sh
python3 dist/statemediafm.pyz genmusic --repo /path/to/repo
```

A quiet repo idles calm and dark; a busy, multi-contributor repo brightens,
speeds up, and adds a lead and percussion. `--base-intensity` sets the user's
resting energy, `--intensity` overrides the derived value, `--out` writes the
program to a file. The installed server also exposes it at `GET /genmusic` as
JSON (`{text, style, intensity, brainwave_band, fade_ms}`) for a client player
to poll and crossfade between. *(The browser Strudel player + visualizer is the
remaining M2 piece.)*

## Tests

```sh
uv pip install -e ".[dev]"
pytest
```

## Security & trust

State Media FM is meant to be **read before it's run**. The full trust model is in
[SECURITY_MODEL.md](SECURITY_MODEL.md); the honest limitations and hardening
backlog are in [HARDENING_PLAN.md](HARDENING_PLAN.md). In short:

- **Loopback, single-operator.** It binds to `127.0.0.1` and the control API is
  **currently unauthenticated** — built for the person running it on their own
  machine. Do **not** bind it to `0.0.0.0` or a shared host without adding auth
  (tracked in the hardening plan).
- **Offline by default, no telemetry.** There is no phone-home. Every network call
  is a functional, operator-configured one: the news sources you add, the LLM
  gateway you point it at, Spotify (only if you connect it), a one-time
  voice-model download, and two CDN `<script>` loads on the player page.
- **Secrets stay on disk, gitignored.** News-source tokens and Spotify credentials
  live in `statemediafm.auth.toml` (written `0600`, gitignored, masked in the UI) —
  treat it as account-equivalent and never commit it. Scope every token
  **read-only / least-privilege**; the Auth panel documents the minimum per provider.
- **Untrusted input is never executed.** Source text is voiced and displayed, never
  run; it is delimited/length-capped before the news prompt and reduced to a numeric
  hash before the music. Prompt-injection defense relies on your chosen gateway/model.

## Layout

```
src/statemediafm/
  core/        data model (§6 contracts) + plan assembly + schedule + director
  sources/     forge issues/MRs, git commits, Hacker News front page → NewsItem
  newsroom/    summarize (LLMClient) + voice (TTSProvider) + themed personas
  genmusic/    activity → ActivitySignal → compose → Strudel (IR + generators)
  web/         FastAPI control API + browser player (news, music, Spotify)
  serve.py     live loop: refresh sources, re-voice, compose, fill song slots
  configstore.py persisted, non-secret UI settings (statemediafm.config.toml)
  spotify.py   Spotify connector: catalogue search + user OAuth + playback wiring
  songs.py     between-news song slots (generic mood/genre search seeds)
  auth.py      gitignored token/endpoint store (statemediafm.auth.toml)
  licensing.py open-core entitlements (commercial modules; verification stubbed)
  pipeline.py  NewsItems → summarize → voice → BroadcastPlan
  cli.py       demo / genmusic / broadcast / serve / rundown
```
