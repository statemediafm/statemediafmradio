# State Media FM

**Why.** Your team throws off a constant stream of activity — issues, merge
requests, deploys, chatter — and keeping an eye on it means living in dashboards.
State Media FM turns that activity into a low-key **internal radio station**: calm
focus music with the occasional spoken news bulletin, so you stay aware without
staring at a screen.

**What.** It's a small app you run on your own machine. It reads your team's tools
(GitHub / GitLab, Hacker News, and more), writes short news updates, reads them
aloud, and plays them over generative background music in a **browser tab**. It's
free and open-source (Apache-2.0), works offline, and phones nothing home.

**How.** You run **one command**; it starts a local web server and opens a browser
tab. You point it at your sources — and, optionally, an AI gateway for richer news
writing — right in the **Settings** tab. No config files to edit, and everything you
set is remembered next time.

## Run it

You need **Python 3.12+** and **git**. Copy-paste:

```sh
git clone https://github.com/statemediafm/statemediafmradio.git
cd statemediafmradio
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[all]"
statemediafm
```

That's it — your browser opens to **http://127.0.0.1:8150**. (If it doesn't open on
its own, just visit that address.)

## Set it going

1. Click **▶ Start** — browsers stay silent until you click, so this begins the audio.
   It's already playing generative music with a Hacker News bulletin on a timer.
2. To add your own team's activity, open the **Settings** tab:
   - **Config** — paste your GitLab (or GitHub) address and a **read-only** access
     token. Optionally add an **AI gateway** URL + key for smarter news writing.
   - **News Update Sources** — add your project's URL.
   - **News-parsing model** — flip **Live news** on and pick a model (optional; with
     it off you still get clear, plain summaries).
3. Everything you change is **saved automatically**. Next time, just run
   `statemediafm` again and it all comes back.

To stop it, press **Ctrl-C** in the terminal.

---

Want the details — other install options, the command-line tools, how the music and
news are generated, the local API, and the architecture? See
**[TECHNICAL.md](TECHNICAL.md)**. For the trust model (what it protects, what stays
on your machine, how tokens are stored), see **[SECURITY_MODEL.md](SECURITY_MODEL.md)**.
