"""Maelcom CLI.

    maelcom demo --repo <URL-or-path>       # one voiced news segment from a repo
    maelcom demo --hn                        # ...from the Hacker News front page
    maelcom genmusic --repo <URL-or-path>    # generative Strudel program
    maelcom broadcast --hn --repo <URL>      # multi-source, timed segment rundown

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
from datetime import datetime, timedelta

from .core.models import Script
from .core.plan import single_news_plan
from .core.schedule import Cadence, Programme, assemble_broadcast, parse_duration
from .genmusic import THETA_START, activity, compose
from .newsroom.llm import LiteLLMClient, load_model_config
from .newsroom.summarize import naive_radio_script, summarize
from .newsroom.tts import PiperTTS, ToneWavTTS, TTSProvider, concat_wavs
from .roster import build_roster, load_config
from .sources import HackerNewsSource, Source, open_source


def _source_items(args: argparse.Namespace) -> list | None:
    """Poll the selected single source: Hacker News with --hn, else the repo."""
    if getattr(args, "hn", False):
        return HackerNewsSource(max_count=args.max_count).poll()
    if args.repo:
        return open_source(args.repo, max_count=args.max_count, token=args.token).poll()
    return None


def _make_tts(args: argparse.Namespace) -> TTSProvider | None:
    """Build the TTS provider, or print guidance and return None on failure."""
    if not getattr(args, "speak", False):
        return ToneWavTTS()
    try:
        return PiperTTS(voice=args.voice)
    except ImportError:
        print("--speak needs the [tts] extra: pip install -e '.[tts]'", file=sys.stderr)
        return None


def _script_for(items: list, style: str, args: argparse.Namespace) -> Script:
    """Offline deterministic summary, or a Claude summary with --live."""
    if getattr(args, "live", False):
        return summarize(items, style, client=LiteLLMClient(), cfg=load_model_config(args.profile))
    return Script(text=naive_radio_script(items, style), style=style)


def _demo(args: argparse.Namespace) -> int:
    items = _source_items(args)
    if items is None:
        print("Give a source: --repo or --hn.", file=sys.stderr)
        return 2
    if not items:
        print("No activity found for this source.", file=sys.stderr)
        return 1
    tts = _make_tts(args)
    if tts is None:
        return 2

    script = _script_for(items, args.style, args)
    audio = tts.render(script)
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


def _ad_hoc_roster(args: argparse.Namespace) -> list[tuple[str, Source, Cadence]]:
    """Roster from --hn/--repo, all on --every, auto-staggered to interleave."""
    every = parse_duration(args.every)
    sources: list[tuple[str, Source]] = []
    if args.hn:
        sources.append(("Hacker News front page", HackerNewsSource(max_count=args.max_count)))
    if args.repo:
        sources.append(
            ("Repository activity", open_source(args.repo, max_count=args.max_count, token=args.token))
        )
    n = len(sources)
    return [
        (topic, source, Cadence(every, i * every / n))
        for i, (topic, source) in enumerate(sources)
    ]


def _broadcast(args: argparse.Namespace) -> int:
    # The roster (which sources air, how often, staggered by what) comes from a
    # config file, or is built ad hoc from --hn/--repo on a shared --every.
    roster: list[tuple[str, Source, Cadence]]
    if args.config:
        try:
            roster = build_roster(load_config(args.config))
        except (OSError, ValueError, KeyError, tomllib.TOMLDecodeError) as exc:
            print(f"roster config error: {exc}", file=sys.stderr)
            return 2
    else:
        roster = _ad_hoc_roster(args)
    if not roster:
        print("Give a roster: --config FILE, or --hn and/or --repo.", file=sys.stderr)
        return 2
    tts = _make_tts(args)
    if tts is None:
        return 2

    programmes: list[Programme] = []
    content: dict[str, tuple[Script, object]] = {}
    for topic, source, cadence in roster:
        items = source.poll()
        if not items:
            print(f"(skipping {topic}: no activity)", file=sys.stderr)
            continue
        script = _script_for(items, args.style, args)
        content[topic] = (script, tts.render(script))
        programmes.append(Programme(topic, cadence))
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
    p.add_argument("--style", default="bbc-world", help="Voice/writing style.")
    p.add_argument(
        "--speak",
        action="store_true",
        help="Real speech via Piper (needs the [tts] extra); default is a placeholder tone.",
    )
    p.add_argument(
        "--voice",
        default="alan",
        metavar="VOICE",
        help="Voice for --speak: alan (British male, default), alba (Scottish "
        "female), northern_english_male, southern_english_female — or a full "
        "Piper name or path to a .onnx model.",
    )
    p.add_argument("--live", action="store_true", help="Summarize via the local Claude client.")
    p.add_argument("--profile", default=None, help="model_config.yaml profile (with --live).")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="maelcom")
    sub = parser.add_subparsers(dest="command", required=True)

    demo = sub.add_parser("demo", help="Summarize a source's recent activity into one segment.")
    _add_source_args(demo)
    _add_voice_args(demo)
    demo.add_argument("--out", default="maelcom-demo.wav", help="Audio output path.")
    demo.set_defaults(func=_demo)

    gm = sub.add_parser("genmusic", help="Turn a source's activity into a generative Strudel program.")
    _add_source_args(gm)
    gm.add_argument("--style", default="lofi", help="Generative style (M2: lofi).")
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
        "--out", default=None, help="Write one combined WAV of all segments back to back."
    )
    bc.add_argument("--out-dir", default=None, help="Directory to write one WAV per segment topic.")
    bc.set_defaults(func=_broadcast)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
