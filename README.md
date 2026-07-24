# Maelcom

Internal streaming radio built on a team's collaboration, project, and
version-control data. It turns activity (git, Slack, Jira, Grafana, …) into a
voiced news broadcast, with generative music that tracks project activity.

See [PLAN.md](PLAN.md) for the architecture and roadmap. This repo currently
implements the **M1 vertical slice**: a repository's recent activity (a
GitHub/GitLab project's issues and merge/pull requests with their latest
comments, or a local repo's commits) → summarized radio script → voiced audio
→ a one-segment broadcast plan.

## Quick start

### Standalone, zero dependencies

The offline demo path uses only the Python standard library, so it ships as a
single-file zipapp — no install, no `pip`, no `PYTHONPATH`:

```sh
./scripts/build_standalone.sh
python3 dist/maelcom.pyz demo --repo /path/to/a/git/repo
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
  API). Try it with `python3 dist/maelcom.pyz demo --hn`.

It writes a radio script to stdout and saves the voiced audio to
`maelcom-demo.wav`. Copy `dist/maelcom.pyz` anywhere and run it with just
`python3`. Passing **both** `--hn` and `--repo` combines them into one segment,
covering each source in full before the next (depth-first, not interleaved),
with each source's headlines attributed and voiced in its own voice.

The offline demo builds a deterministic summary from the real activity (top
contributors + recent headlines) and voices it with a placeholder tone — so it
needs no credentials or model, yet the output reflects the project.

### Real spoken audio

The tone is a stand-in. For actual speech, install the `[tts]` extra (offline
neural TTS via [Piper](https://github.com/OHF-Voice/piper1-gpl)) and add
`--speak`:

```sh
uv pip install -e ".[tts]"          # or: pip install -e ".[tts]"
maelcom demo --repo /path/to/repo --speak --out news.wav
```

The first run downloads a small voice model (~60 MB) into `./voices/`
(override with `MAELCOM_VOICES_DIR`); later runs are offline. Pick a voice with
`--voice`:

| Alias | Voice |
|---|---|
| `alan` (default) | British male |
| `alba` | British (Scottish) female |
| `northern_english_male` | Northern English male |
| `southern_english_female` | Southern English female |

You can also pass a full Piper name (e.g. `en_US-lessac-medium`) or a path to
your own `.onnx` model. `--speak` needs a normal install — it is not in the
zero-dependency zipapp.

### Installed (real models + web)

```sh
uv pip install -e ".[all]"          # or: pip install -e ".[all]"

# Real summaries via the local Claude client (LiteLLM → anthropic/claude-opus-4-8).
# Auth: set ANTHROPIC_API_KEY (or ANTHROPIC_AUTH_TOKEN) in the environment.
maelcom demo --repo /path/to/repo --live
```

Extras: `.[llm]` (LiteLLM + config), `.[tts]` (Piper neural speech), `.[web]`
(FastAPI + uvicorn), `.[dev]` (pytest, ruff, mypy), `.[all]`.

## Multiple sources as timed segments

`broadcast` airs several sources at **different times**, each on its own cadence,
so they read as distinct news segments about their topic (a step toward the
"rhythm of the day" scheduler):

```sh
# Ad hoc: sources on a shared interval, auto-staggered to interleave.
maelcom broadcast --hn --repo https://github.com/meltano/meltano --every 15m --window 60
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
maelcom broadcast --config examples/roster.toml --window 60
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
`maelcom broadcast --hn --window 120` alone drops a `news.wav` in the current
directory. Add `--speak` for real voices —
each segment is voiced in a **different** rotating voice so the topics sound
distinct. The broadcast opens with a spoken time greeting ("Good day. It is
16:52."), headlines are **attributed** to their source (Hacker News, the git
project) and spaced by `--headline-pause` seconds (default `1.0`).

Durations accept units (`15m`, `90s`, `1h`) or bare seconds. The scheduler
(`core/schedule.py`: `Cadence`, `Programme`, `assemble_broadcast`) is
pure/deterministic — it never reads the wall clock.

## Generative music (M2)

Maelcom also turns a repo's activity into a **Strudel program** — generative
music that tracks the project. Activity becomes an `ActivitySignal` (volume,
volatility, participants, themes); that maps to an intensity on the
brainwave-band scale (sessions start at **theta** and rise toward gamma as
activity climbs), and a style renderer emits Strudel source text:

```sh
python3 dist/maelcom.pyz genmusic --repo /path/to/repo
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

## Layout

```
src/maelcom/
  core/       data model (§6 contracts) + plan assembly + schedule (cadences)
  sources/    forge issues/MRs, git commits, Hacker News front page → NewsItem
  newsroom/   summarize (LLMClient) + voice (TTSProvider)
  genmusic/   activity → ActivitySignal → compose → StrudelProgram (lofi)
  web/        FastAPI: /health, /plan, /audio/{id}, /genmusic, Tufte page
  pipeline.py NewsItems → summarize → voice → BroadcastPlan
  cli.py      `maelcom demo`, `maelcom genmusic`, `maelcom broadcast`
```
