"""State Media FM CLI.

    statemediafm demo --repo <URL-or-path>       # one voiced news segment from a repo
    statemediafm demo --hn                        # ...from the Hacker News front page
    statemediafm genmusic --repo <URL-or-path>    # generative Strudel program
    statemediafm broadcast --hn --repo <URL>      # multi-source, timed segment rundown

Sources: a GitHub/GitLab URL (issues + merge/pull requests with latest
comments), a local/bare repo (recent commits), or the Hacker News front page
(``--hn``). ``broadcast`` airs several sources at different times, so each reads
as its own news segment about that topic.
"""

from __future__ import annotations

import argparse
import os
import sys
import tomllib
import urllib.error
from dataclasses import replace
from datetime import datetime, timedelta

from . import serve as serve_mod
from .configstore import load_config as load_station_config
from .core.models import AudioRef, Script
from .core.plan import single_news_plan
from .core.schedule import Cadence, Programme, assemble_broadcast, parse_duration
from .genmusic import THETA_START, activity, compose
from .newsroom.llm import LiteLLMClient, llm_config
from .newsroom.summarize import radio_reads, summarize, time_greeting
from .newsroom.tts import PiperTTS, ToneWavTTS, TTSProvider, concat_wavs, render_reads
from .roster import (
    build_roster,
    build_segment,
    genmusic_settings,
    llm_settings,
    load_config,
    load_source_plugins,
)
from .sources import HackerNewsSource, Source, detect_forge, open_source

# Voices rotated across broadcast segments so each topic/source sounds distinct.
# All are Piper *medium* voices (22.05 kHz) so segments concatenate cleanly.
_SPEAK_VOICES = ("alan", "alba", "northern_english_male")
# For the offline tone voice, vary pitch per segment instead (A3, C4, G3).
_TONE_FREQS = (220.0, 262.0, 196.0)


class _CliError(Exception):
    """A user-facing error — reported as a clean message, not a traceback."""


def _poll(source: Source) -> list:
    """Poll a source, turning network/API failures into a clean CLI error."""
    try:
        return source.poll()
    except urllib.error.HTTPError as exc:
        hint = ""
        if exc.code in (401, 403):
            hint = " — set GITHUB_TOKEN / GITLAB_TOKEN (or --token), or retry later"
        raise _CliError(f"{type(source).__name__}: HTTP {exc.code} {exc.reason}{hint}") from exc
    except urllib.error.URLError as exc:
        raise _CliError(f"{type(source).__name__}: cannot reach network ({exc.reason})") from exc


def _source_items(args: argparse.Namespace) -> list | None:
    """Poll the selected sources into one item list.

    With both --hn and --repo, the two are combined into a single segment,
    concatenated (not interleaved) so the summary covers one source in full
    before the next. Returns ``None`` if no source was selected.
    """
    items: list = []
    picked = False
    if getattr(args, "hn", False):
        items += _poll(HackerNewsSource(max_count=args.max_count))
        picked = True
    if args.repo:
        # Honor a configured self-hosted GitLab instance ([gitlab] endpoint), and a
        # UI-saved GitLab token when no --token is given, so `demo` matches `serve`.
        from .auth import source_endpoint, source_token

        gitlab_base = source_endpoint("gitlab") or None
        token = args.token
        if token is None and (detect_forge(args.repo, gitlab_base=gitlab_base) or (None,))[0] == "gitlab":
            token = source_token("gitlab")
        items += _poll(
            open_source(args.repo, max_count=args.max_count, token=token, gitlab_base=gitlab_base)
        )
        picked = True
    return items if picked else None


def _piper_or_tone(args: argparse.Namespace, *, voice: str, tone_freq: float) -> TTSProvider:
    """Real speech (Piper) by default; the placeholder tone with ``--tone`` or
    when the ``[tts]`` extra isn't installed (so the zipapp still runs)."""
    if not getattr(args, "tone", False):
        try:
            return PiperTTS(voice=voice)
        except ImportError:
            if not getattr(args, "_tone_warned", False):
                print(
                    "(no [tts] extra — using the placeholder tone; "
                    "run  pip install -e '.[tts]'  for real voices)",
                    file=sys.stderr,
                )
                args._tone_warned = True
    return ToneWavTTS(frequency=tone_freq)


def _segment_tts(args: argparse.Namespace, index: int) -> TTSProvider:
    """A voice for segment ``index`` — rotating so topics sound distinct."""
    return _piper_or_tone(
        args,
        voice=_SPEAK_VOICES[index % len(_SPEAK_VOICES)],
        tone_freq=_TONE_FREQS[index % len(_TONE_FREQS)],
    )


def _llm_config(args: argparse.Namespace):
    """LLMConfig for ``--live``: the ``[llm]`` table from ``--config`` (if any),
    overlaid with ``--profile``, else the ``model_config.yaml`` profile. The
    gateway URL/key fall back to the ``llm-gateway`` auth slot at call time."""
    cfg_path = getattr(args, "config", None)
    settings = llm_settings(load_config(cfg_path)) if cfg_path else {}
    return llm_config(settings, profile=getattr(args, "profile", None))


def _voice_segment(
    items: list,
    args: argparse.Namespace,
    tts: TTSProvider,
    *,
    greeting: str | None = None,
    max_headlines: int | None = None,
) -> tuple[Script, AudioRef]:
    """Summarize one source and voice it, pausing between headlines.

    Offline uses the chunked ``radio_reads`` so ``render_reads`` can space the
    headlines; ``--live`` returns a single blob (no inter-headline pause).
    ``max_headlines`` overrides the CLI ``--headlines`` default (per-segment).
    """
    style = args.style
    if getattr(args, "live", False):
        script = summarize(items, style, client=LiteLLMClient(), cfg=_llm_config(args))
        if greeting:
            script = replace(script, text=f"{greeting} {script.text}")
        return script, tts.render(script)

    limit = args.headlines if max_headlines is None else max_headlines
    reads = radio_reads(items, style, greeting=greeting, max_headlines=limit)
    script = Script(text=" ".join(read.text for read in reads), style=style)
    pause_ms = round(args.headline_pause * 1000)

    # When a single segment mixes sources, give each source its own voice so the
    # headlines switch voice mid-segment. Single-source segments keep one voice.
    origins = sorted({r.origin for r in reads if r.role == "headline" and r.origin})
    voice_for = None
    if len(origins) > 1:
        providers = {origin: _segment_tts(args, i) for i, origin in enumerate(origins)}
        voice_for = providers.get

    audio = render_reads(
        reads, tts, style=style, headline_pause_ms=pause_ms, voice_for=voice_for
    )
    return script, audio


def _demo(args: argparse.Namespace) -> int:
    items = _source_items(args)
    if items is None:
        print("Give a source: --repo or --hn.", file=sys.stderr)
        return 2
    if not items:
        print("No activity found for this source.", file=sys.stderr)
        return 1
    tts = _piper_or_tone(args, voice=args.voice, tone_freq=_TONE_FREQS[0])
    greeting = time_greeting(datetime.now().astimezone())
    script, audio = _voice_segment(items, args, tts, greeting=greeting)
    segment = single_news_plan(audio, script).segments[0]

    print(f"— {len(items)} items → {segment.duration_s:.0f}s segment ({args.style}) —\n")
    print(segment.script.text if segment.script else "(no script)")
    if segment.audio:
        with open(args.out, "wb") as fh:
            fh.write(segment.audio.data)
        print(f"\nAudio written to {args.out} ({len(segment.audio.data)} bytes)")
    return 0


def _genmusic(args: argparse.Namespace) -> int:
    items = _source_items(args)
    if items is None:
        print("Give a source: --repo or --hn.", file=sys.stderr)
        return 2
    if not items:
        print("No activity found for this source.", file=sys.stderr)
        return 1

    signal = activity(items)
    try:
        program = compose(
            signal,
            style=args.style,
            intensity=args.intensity,
            base_intensity=args.base_intensity,
        )
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    print(
        f"— {signal.volume} items → {program.style} @ {program.brainwave_band} "
        f"(intensity {program.intensity:.2f}), {signal.participant_count} voices —\n"
    )
    print(program.text)
    if args.out:
        with open(args.out, "w") as fh:
            fh.write(program.text + "\n")
        print(f"\nStrudel program written to {args.out}")
    return 0


def _ad_hoc_roster(args: argparse.Namespace) -> list[tuple[str, Source, Cadence, int | None]]:
    """Roster from --hn/--repo, all on --every, auto-staggered. Headlines is None
    (use the CLI --headlines default) since ad-hoc has no per-segment config."""
    every = parse_duration(args.every)
    sources: list[tuple[str, Source]] = []
    if args.hn or not args.repo:  # default to HN unless only --repo was given
        sources.append(("Hacker News front page", HackerNewsSource(max_count=args.max_count)))
    if args.repo:
        from .auth import source_endpoint, source_token

        gitlab_base = source_endpoint("gitlab") or None
        token = args.token
        if token is None and (detect_forge(args.repo, gitlab_base=gitlab_base) or (None,))[0] == "gitlab":
            token = source_token("gitlab")
        sources.append(
            (
                "Repository activity",
                open_source(args.repo, max_count=args.max_count, token=token, gitlab_base=gitlab_base),
            )
        )
    n = len(sources)
    return [
        (topic, source, Cadence(every, i * every / n), None)
        for i, (topic, source) in enumerate(sources)
    ]


def _resolve_roster(args: argparse.Namespace) -> list:
    """Roster from --config, else ad-hoc from --hn/--repo. Raises _CliError on a
    bad config; returns [] when no source was given."""
    if args.config:
        try:
            config = load_config(args.config)
            load_source_plugins(config)  # register custom source kinds first
            return build_roster(config)
        except (OSError, ValueError, KeyError, tomllib.TOMLDecodeError) as exc:
            raise _CliError(f"roster config error: {exc}") from exc
    return _ad_hoc_roster(args)


def _ad_hoc_segments(args: argparse.Namespace) -> list[dict]:
    """Ad-hoc --hn/--repo as segment dicts, on --every and auto-staggered — the
    same shape as config ``[[segments]]`` so serve can manage them live. With no
    source given at all, the Hacker News front page is the zero-config default."""
    segs: list[dict] = []
    if args.hn or not args.repo:  # default to HN unless only --repo was given
        segs.append({"topic": "Hacker News front page", "source": "hackernews",
                     "max_count": args.max_count, "every": args.every})
    if args.repo:
        seg = {"topic": "Repository activity", "source": "repo", "repo": args.repo,
               "max_count": args.max_count, "every": args.every}
        if args.token:
            seg["token"] = args.token
        segs.append(seg)
    every, n = parse_duration(args.every), len(segs) or 1
    for i, seg in enumerate(segs):
        seg["offset"] = i * every / n
    return segs


def _resolve_segments(args: argparse.Namespace, persisted: dict | None = None) -> list[dict]:
    """The roster as segment dicts. Precedence: an explicit ``--config`` file, then
    ``--hn``/``--repo`` flags, then the **persisted** sources
    (``statemediafm.config.toml``), then the zero-config Hacker News default. Raises
    _CliError on a bad ``--config``."""
    if args.config:
        try:
            config = load_config(args.config)
            load_source_plugins(config)
            segs = config.get("segments")
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise _CliError(f"roster config error: {exc}") from exc
        if not segs:
            raise _CliError("roster config has no 'segments'")
        return list(segs)
    if args.hn or args.repo:
        return _ad_hoc_segments(args)  # explicit source flags win over the persisted roster
    persisted_sources = (persisted or {}).get("sources")
    if persisted_sources:
        return [dict(s) for s in persisted_sources]  # restore the UI-managed roster
    return _ad_hoc_segments(args)  # nothing configured → the Hacker News default


def _broadcast(args: argparse.Namespace) -> int:
    # The roster (which sources air, how often, staggered by what) comes from a
    # config file, or is built ad hoc from --hn/--repo on a shared --every.
    roster = _resolve_roster(args)
    if not roster:
        print("Give a roster: --config FILE, or --hn and/or --repo.", file=sys.stderr)
        return 2

    # Each segment gets its own voice (rotated), and the broadcast opens with the
    # time greeting on the first segment. Headlines are paced apart within each.
    programmes: list[Programme] = []
    content: dict[str, tuple[Script, AudioRef]] = {}
    seg_index = 0
    for topic, source, cadence, headlines in roster:
        try:
            items = _poll(source)
        except _CliError as exc:
            print(f"(skipping {topic}: {exc})", file=sys.stderr)
            continue
        if not items:
            print(f"(skipping {topic}: no activity)", file=sys.stderr)
            continue
        seg_tts = _segment_tts(args, seg_index)
        greeting = time_greeting(datetime.now().astimezone()) if seg_index == 0 else None
        content[topic] = _voice_segment(
            items, args, seg_tts, greeting=greeting, max_headlines=headlines
        )
        programmes.append(Programme(topic, cadence))
        seg_index += 1
    if not programmes:
        print("No segments to air.", file=sys.stderr)
        return 1

    plan = assemble_broadcast(programmes, content, args.window * 60)
    now = datetime.now().astimezone()  # local wall clock, for display only

    print(f"Broadcast rundown — next {args.window} min, {len(plan.segments)} segments:\n")
    for seg in plan.segments:
        air = now + timedelta(seconds=seg.start_s)
        print(f"  {air:%H:%M}  {seg.title or '':24}  {seg.duration_s:4.0f}s")

    print("\nSegment scripts:")
    for topic, (script, _audio) in content.items():
        print(f"\n— {topic} —\n{script.text}")

    # One combined WAV of all segments back to back (each topic once, in order).
    if args.out:
        combined = concat_wavs([audio for _script, audio in content.values()])
        with open(args.out, "wb") as fh:
            fh.write(combined.data)
        print(f"\nCombined broadcast written to {args.out} ({combined.duration_ms / 1000:.0f}s)")

    # One WAV per segment topic.
    if args.out_dir:
        os.makedirs(args.out_dir, exist_ok=True)
        for topic, (_script, audio) in content.items():
            slug = "".join(c if c.isalnum() else "-" for c in topic.lower()).strip("-")
            path = os.path.join(args.out_dir, f"{slug}.wav")
            with open(path, "wb") as fh:
                fh.write(audio.data)
            print(f"wrote {path}")
    return 0


def _serve(args: argparse.Namespace) -> int:
    # Persisted UI settings (statemediafm.config.toml) — the memory that makes a
    # no-flags run reproduce the last session. Flags still win (see below).
    persisted = load_station_config()
    pstation = persisted.get("station", {}) if persisted else {}
    pmix = persisted.get("mix", {}) if persisted else {}
    pnews = persisted.get("news", {}) if persisted else {}
    segments = _resolve_segments(args, persisted)
    if not segments:
        print("Give a roster: --config FILE, or --hn and/or --repo.", file=sys.stderr)
        return 2
    try:
        roster = [build_segment(seg, i) for i, seg in enumerate(segments)]
    except (ValueError, KeyError) as exc:
        raise _CliError(f"roster config error: {exc}") from exc
    # Precedence: an explicit flag > the persisted setting > the built-in default.
    # (The serve subparser defaults --voice/--style to None so an omitted flag
    # falls through to the persisted value instead of masking it.)
    voice = args.voice or pstation.get("voice") or "alan"
    style = args.style or pstation.get("style") or "newsroom"
    tts = _piper_or_tone(args, voice=voice, tone_freq=_TONE_FREQS[0])
    config = load_config(args.config) if args.config else {}
    # The ambient generator: --generator > persisted > [genmusic] config > default.
    gm = genmusic_settings(config)
    generator = args.generator or pstation.get("generator") or gm["generator"]
    # LLM news. `live` may come from --live OR the persisted toggle; either way it's
    # also switchable at runtime from Settings (/news-live). A base news_cfg is
    # always built (a plain LLMConfig — no litellm needed) so the model picker +
    # gateway Discover work; the client is constructed lazily only when live.
    live = bool(getattr(args, "live", False)) or bool(pnews.get("live", False))
    news_cfg = _llm_config(args)
    news_models = llm_settings(config).get("models") or []
    # Cadences: flag > persisted > default (news 17m, refresh 60s).
    if args.news_every:
        news_every_s = parse_duration(args.news_every)
    elif pstation.get("news_every_s"):
        news_every_s = float(pstation["news_every_s"])
    else:
        news_every_s = parse_duration("17m")
    if args.refresh is not None:
        refresh = float(args.refresh)
    else:
        refresh = float(pstation.get("refresh_s") or 60.0)
    return serve_mod.run(
        roster,
        tts,
        host=args.host,
        port=args.port,
        refresh=refresh,
        headline_pause_ms=round(args.headline_pause * 1000),
        style=style,
        generator=generator,
        show_selector=gm["selector"],
        generators_dir=gm["generators_dir"],
        news_models=news_models,
        segments=segments,
        voice=voice,
        news_every_s=news_every_s,
        base_intensity=pstation.get("base_intensity"),
        quiet_mode=bool(pstation.get("quiet_mode", False)),
        mix=pmix,
        persist=True,  # write UI changes back to statemediafm.config.toml
        live=live,
        # Shipped default: the local Claude CLI (the operator's own login, no key).
        news_backend=pnews.get("backend") or "claude-cli",
        news_cfg=news_cfg,
        news_model=pnews.get("model") or None,
        news_temperature=pnews.get("temperature"),
        news_max_tokens=pnews.get("max_tokens"),
        open_browser=not getattr(args, "no_open", False),
    )


def _add_source_args(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--repo",
        default=None,
        help="GitHub/GitLab URL (issues + merge/pull requests with latest "
        "comments) OR a local/bare repo path (recent commits).",
    )
    p.add_argument("--hn", action="store_true", help="Use the Hacker News front page as a source.")
    p.add_argument("--max-count", type=int, default=25, help="Items to read per source.")
    p.add_argument(
        "--token",
        default=None,
        help="Forge API token (else GITHUB_TOKEN / GITLAB_TOKEN); raises rate limits.",
    )


def _add_voice_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--style", default="newsroom", help="Voice/writing style.")
    p.add_argument(
        "--tone",
        action="store_true",
        help="Use the placeholder tone instead of real speech (speech is the default).",
    )
    p.add_argument(
        "--speak",
        action="store_true",
        help=argparse.SUPPRESS,  # deprecated: speech is now the default (kept for compatibility)
    )
    p.add_argument(
        "--voice",
        default="alan",
        metavar="VOICE",
        help="Speech voice: alan (British male, default), alba (Scottish "
        "female), northern_english_male, southern_english_female — or a full "
        "Piper name or path to a .onnx model.",
    )
    p.add_argument("--live", action="store_true", help="Summarize via the local Claude client.")
    p.add_argument("--profile", default=None, help="model_config.yaml profile (with --live).")
    p.add_argument(
        "--headline-pause",
        type=float,
        default=1.0,
        help="Seconds of silence between spoken headlines (default 1.0).",
    )
    p.add_argument(
        "--headlines",
        type=int,
        default=5,
        help="Max headlines read per source (default 5).",
    )


def _rundown(args: argparse.Namespace) -> int:
    """Print the 'hour of radio' running order — the rhythm of the day (M4).

    The Director lays out news bulletins on their cadence with song slots between
    and station idents bridging any gap, so nothing in the foreground falls quiet
    for more than a few minutes; the generative music plays continuously beneath.
    """
    from .core.director import FELT_MAX_GAP_S, Director

    director = Director(news=Cadence(parse_duration(args.news_every)))
    window_s = args.window * 60.0
    start = datetime.now().astimezone()
    order = director.running_order(window_s)

    label = {
        "news": "● News bulletin",
        "song": "♪ Song slot   [reserved · M5]",
        "ident": "· Station ident (time check)",
    }
    end = start + timedelta(seconds=window_s)
    print(f"State Media FM — hour of radio  ({start:%H:%M}–{end:%H:%M})")
    print(
        f"news every {args.news_every} · music under · idents keep a foreground "
        f"beat at least every {int(FELT_MAX_GAP_S // 60)}m\n"
    )
    for cue in order:
        at = start + timedelta(seconds=cue.at_s)
        print(f"  {at:%H:%M}  {label.get(cue.kind, cue.kind)}")

    # Prove the felt cadence: no gap between foreground beats exceeds the cap.
    times = [0.0] + [c.at_s for c in order] + [window_s]
    max_gap = max(times[i + 1] - times[i] for i in range(len(times) - 1))
    ok = "OK" if max_gap <= FELT_MAX_GAP_S + 1e-6 else "TOO LONG"
    print("\n  Generative music plays continuously beneath, ducking under each read.")
    print(f"  Felt cadence: longest quiet gap {max_gap / 60:.1f}m [{ok}]")
    return 0


_SUBCOMMANDS = ("demo", "genmusic", "broadcast", "serve", "rundown")


def main(argv: list[str] | None = None) -> int:
    # Zero-config: `statemediafm` (no subcommand) runs the station. If the first
    # token isn't a subcommand or a help/exit flag, default to `serve` so bare
    # flags (e.g. `statemediafm --port 8150`) work too.
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or (argv[0] not in _SUBCOMMANDS and argv[0] not in ("-h", "--help")):
        argv = ["serve", *argv]

    parser = argparse.ArgumentParser(prog="statemediafm")
    sub = parser.add_subparsers(dest="command", required=True)

    demo = sub.add_parser("demo", help="Summarize a source's recent activity into one segment.")
    _add_source_args(demo)
    _add_voice_args(demo)
    demo.add_argument("--out", default="statemediafm-demo.wav", help="Audio output path.")
    demo.set_defaults(func=_demo)

    gm = sub.add_parser("genmusic", help="Turn a source's activity into a generative Strudel program.")
    _add_source_args(gm)
    gm.add_argument(
        "--style", default="tintinnabuli", help="Generative style: tintinnabuli (default) or lofi."
    )
    gm.add_argument(
        "--base-intensity",
        type=float,
        default=THETA_START,
        help="User base energy 0..1; sessions start at theta (default).",
    )
    gm.add_argument(
        "--intensity", type=float, default=None, help="Override the activity-derived intensity (0..1)."
    )
    gm.add_argument("--out", default=None, help="Write the Strudel program to this file.")
    gm.set_defaults(func=_genmusic)

    bc = sub.add_parser(
        "broadcast", help="Air several sources at different times as a timed segment rundown."
    )
    _add_source_args(bc)
    _add_voice_args(bc)
    bc.add_argument(
        "--config",
        default=None,
        metavar="FILE",
        help="Roster file (.toml/.json): per-segment source + cadence. Overrides --hn/--repo.",
    )
    bc.add_argument(
        "--every",
        default="15m",
        help="Ad-hoc cadence interval for --hn/--repo (e.g. 15m, 90s, 1h). Sources auto-stagger.",
    )
    bc.add_argument("--window", type=int, default=60, help="Rundown length in minutes.")
    bc.add_argument(
        "--out",
        default="news.wav",
        help="Combined WAV of all segments back to back (default news.wav; '' to skip).",
    )
    bc.add_argument("--out-dir", default=None, help="Directory to write one WAV per segment topic.")
    bc.set_defaults(func=_broadcast)

    sv = sub.add_parser(
        "serve", help="Run the web server with a live news + generative-music loop."
    )
    _add_source_args(sv)
    _add_voice_args(sv)
    # For serve, an omitted --voice/--style must fall through to the persisted
    # setting (statemediafm.config.toml), so default them to None here (the shared
    # adders default them for demo/broadcast, which don't persist). --news-every /
    # --refresh get their None defaults on their own add_argument calls below (they
    # are added after this, so set_defaults here wouldn't stick).
    sv.set_defaults(voice=None, style=None)
    sv.add_argument(
        "--config",
        default=None,
        metavar="FILE",
        help="Roster file (.toml/.json): per-segment source + cadence. Overrides --hn/--repo.",
    )
    sv.add_argument(
        "--every",
        default="15m",
        help="Ad-hoc cadence interval for --hn/--repo (e.g. 15m, 90s, 1h). Sources auto-stagger.",
    )
    sv.add_argument(
        "--generator",
        "--ambient",
        default=None,
        metavar="NAME",
        help="Ambient generator to start with (e.g. 'Space Dub', 'Entrainment 0.1'). "
        "Overrides the [genmusic] config; the news bed and opening bulletin use it.",
    )
    sv.add_argument("--host", default="127.0.0.1", help="Bind host (default 127.0.0.1).")
    sv.add_argument("--port", type=int, default=8150, help="Bind port (default 8150).")
    sv.add_argument(
        "--refresh", type=float, default=None,
        help="Seconds between source refreshes (default 60; persisted/editable in Settings).",
    )
    sv.add_argument(
        "--news-every",
        default=None,
        help="News cadence — how often a bulletin airs (default 17m; persisted/editable in Settings).",
    )
    sv.add_argument(
        "--no-open",
        action="store_true",
        help="Don't open the player in a browser on start (default: open on loopback).",
    )
    sv.set_defaults(func=_serve)

    rd = sub.add_parser(
        "rundown", help="Print the 'hour of radio' running order (news / music / song pacing)."
    )
    rd.add_argument(
        "--news-every", default="17m", help="News bulletin cadence (default 17m)."
    )
    rd.add_argument("--window", type=int, default=60, help="Hour length in minutes (default 60).")
    rd.set_defaults(func=_rundown)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except _CliError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
