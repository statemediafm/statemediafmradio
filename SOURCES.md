# News sources — add your own

Maelcom polls **sources** (each returns `NewsItem`s), summarizes them, and voices
a broadcast. Built-in kinds: `hackernews` and `repo` (GitHub/GitLab issues + PRs,
or a local git repo's commits). You can add more — Jira, Slack, PagerDuty, an
internal API — without editing core.

## The roster picks sources by *kind*

Your `--config` file lists segments, each naming a source `kind`:

```toml
[[segments]]
topic  = "Hacker News front page"
source = "hackernews"
every  = "15m"

[[segments]]
topic  = "Engineering"
source = "repo"
repo   = "https://github.com/your-org/your-repo"
every  = "15m"
```

## Adding a new source kind

A source is a class implementing `poll(since=None) -> list[NewsItem]` (see
`maelcom/sources/`). Register a **builder** — `build(topic, seg) -> Source` — for
your kind, either in code:

```python
from maelcom.roster import register_source_kind
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
(`maelcom.auth.toml`, or `$MAELCOM_AUTH`), edited from the **Settings** tab in the
UI. Tokens are masked in the UI, written owner-only, and never committed. A
builder reads them via `maelcom.auth`:

```python
from maelcom.auth import source_token, source_endpoint
token = source_token("jira")          # the saved token, or None
base  = source_endpoint("jira")       # the saved endpoint, or None
```

The built-in `repo` source already falls back to the saved `github` / `gitlab`
token when no `token_env` is set.
