# News sources — add your own

State Media FM polls **sources** (each returns `NewsItem`s), summarizes them, and voices
a broadcast. Built-in kinds: `hackernews`, `repo` (GitHub/GitLab issues + PRs, or
a local git repo's commits), `slack` (a channel's recent messages), `jira` (a
project's recent issues), and `pagerduty` (recent incidents). You can add more —
an internal API, etc. — without editing core.

## Jira

```toml
[[segments]]
topic   = "Backlog"
source  = "jira"
project = "OPS"          # the project key
max_count = 25
every   = "15m"
```

In **Settings → jira**: set the **endpoint** to your site
(`https://your-org.atlassian.net`) and the **token** to your `email:api_token`
pair (Jira Cloud uses Basic auth). Reads recently-updated issues, newest first.

## PagerDuty

```toml
[[segments]]
topic    = "Incidents"
source   = "pagerduty"
statuses = ["triggered", "acknowledged"]   # optional; this is the default
max_count = 25
every    = "5m"
```

In **Settings → pagerduty**: set the **token** (a REST API key; endpoint defaults
to `https://api.pagerduty.com`). Reads recent incidents, newest first.

## Slack

```toml
[[segments]]
topic   = "Engineering chat"
source  = "slack"
channel = "eng"          # channel name (no #) or a channel ID (C…)
max_count = 25
every   = "10m"
```

Add the bot token in the **Settings** tab (source `slack`). The bot needs
`channels:history` / `groups:history`, `channels:read`, `users:read`, and to be
a member of the channel. Bot/system messages are skipped and Slack markup is
cleaned to plain text.

## The roster picks sources by *kind*

Your `--config` file lists segments, each naming a source `kind`:

```toml
[[segments]]
topic  = "Hacker News front page"
source = "hackernews"
every  = "15m"

[[segments]]
topic   = "Engineering"
source  = "repo"
repo    = "https://github.com/your-org/your-repo"
max_age = "24h"  # widen the recency window (optional; default 12h)
every   = "15m"
```

For the GitHub/GitLab `repo` source, `repo` may be a project URL **or a pasted
issue / PR / MR URL** (it's normalized to the project). Like a radio, it airs
**recent** activity: each poll returns the work items and comments **updated
since the last poll**, and the first poll (or one after a long gap) reaches back
no further than `max_age` — **12h by default**. Set `max_age` (e.g. `48h`, `7d`)
to widen or narrow that window.

## Adding a new source kind

A source is a class implementing `poll(since=None) -> list[NewsItem]` (see
`statemediafm/sources/`). Register a **builder** — `build(topic, seg) -> Source` — for
your kind, either in code:

```python
from statemediafm.roster import register_source_kind
register_source_kind("jira", lambda topic, seg: MyJiraSource(seg["project"]))
```

…or from config, with a `[[source_plugins]]` entry pointing at a
`"module:function"` builder (registered before the roster is built):

```toml
[[source_plugins]]
kind    = "jira"
builder = "my_sources.jira:build"

[[segments]]
topic   = "Incidents"
source  = "jira"
project = "OPS"
every   = "10m"
```

Your builder reads whatever it needs from `seg` and returns a `Source`.

## Endpoints & auth tokens

Per-source **endpoints and personal tokens** live in a gitignored local file
(`statemediafm.auth.toml`, or `$STATEMEDIAFM_AUTH`), edited from the **Settings** tab in the
UI. Tokens are masked in the UI, written owner-only, and never committed. A
builder reads them via `statemediafm.auth`:

```python
from statemediafm.auth import source_token, source_endpoint
token = source_token("jira")          # the saved token, or None
base  = source_endpoint("jira")       # the saved endpoint, or None
```

The built-in `repo` source already falls back to the saved `github` / `gitlab`
token when no `token_env` is set.

## Managing sources live (serve)

When running `statemediafm serve`, the **Settings** tab has a **Sources** panel that
lists the live roster and lets you add or remove sources without a restart
(pick a kind, fill its one parameter — `channel`, `project`, `repo`, …). Changes
apply to the running session only; they are not written back to the config file.
The refresh loop reads the live roster on each tick, so a newly added source
airs on the next cycle.
