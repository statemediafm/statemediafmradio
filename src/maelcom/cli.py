"""Maelcom CLI — M1 demo entry point.

    maelcom demo --repo /path/to/repo            # offline: deterministic summary
    maelcom demo --repo /path/to/repo --live     # local Claude client via LiteLLM
    maelcom demo --repo /path/to/repo --speak    # real spoken audio via Piper

Points the git source at a repo, summarizes recent commits into a radio script,
voices it, writes the audio, and prints the script.
"""

from __future__ import annotations

import argparse
import sys

from .core.models import Script
from .core.plan import single_news_plan
from .genmusic import THETA_START, activity, compose
from .newsroom.llm import LiteLLMClient, load_model_config
from .newsroom.summarize import naive_radio_script, summarize
from .newsroom.tts import PiperTTS, ToneWavTTS, TTSProvider
from .sources import GitSource


def _demo(args: argparse.Namespace) -> int:
    items = GitSource(args.repo, max_count=args.max_count).poll()
    if not items:
        print(f"No commits found in {args.repo}", file=sys.stderr)
        return 1

    tts: TTSProvider
    if args.speak:
        try:
            tts = PiperTTS(voice=args.voice)
        except ImportError:
            print(
                "--speak needs the [tts] extra: pip install -e '.[tts]'",
                file=sys.stderr,
            )
            return 2
    else:
        tts = ToneWavTTS()

    # Offline builds a deterministic summary from the real commits; --live sends
    # them through the local Claude client for fluent prose.
    if args.live:
        script = summarize(
            items, args.style, client=LiteLLMClient(), cfg=load_model_config(args.profile)
        )
    else:
        script = Script(text=naive_radio_script(items, args.style), style=args.style)

    audio = tts.render(script)
    plan = single_news_plan(audio, script)
    segment = plan.segments[0]

    print(f"— {len(items)} commits → {segment.duration_s:.0f}s segment ({args.style}) —\n")
    print(segment.script.text if segment.script else "(no script)")
    if segment.audio:
        with open(args.out, "wb") as fh:
            fh.write(segment.audio.data)
        print(f"\nAudio written to {args.out} ({len(segment.audio.data)} bytes)")
    return 0


def _genmusic(args: argparse.Namespace) -> int:
    items = GitSource(args.repo, max_count=args.max_count).poll()
    if not items:
        print(f"No commits found in {args.repo}", file=sys.stderr)
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
        f"— {signal.volume} commits → {program.style} @ {program.brainwave_band} "
        f"(intensity {program.intensity:.2f}), {signal.participant_count} voices —\n"
    )
    print(program.text)
    if args.out:
        with open(args.out, "w") as fh:
            fh.write(program.text + "\n")
        print(f"\nStrudel program written to {args.out}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="maelcom")
    sub = parser.add_subparsers(dest="command", required=True)

    demo = sub.add_parser("demo", help="Summarize a git repo's recent activity into radio.")
    demo.add_argument(
        "--repo",
        required=True,
        help="Local path OR remote URL of a git repo "
        "(e.g. https://gitlab.com/meltano/meltano). Remotes are shallow-cloned.",
    )
    demo.add_argument("--style", default="bbc-world", help="Voice/writing style.")
    demo.add_argument("--max-count", type=int, default=50, help="Commits to read.")
    demo.add_argument("--out", default="maelcom-demo.wav", help="Audio output path.")
    demo.add_argument(
        "--speak",
        action="store_true",
        help="Real speech via Piper (needs the [tts] extra); default is a placeholder tone.",
    )
    demo.add_argument(
        "--voice",
        default="alan",
        metavar="VOICE",
        help="Voice for --speak: alan (British male, default), alba (Scottish "
        "female), northern_english_male, southern_english_female — or a full "
        "Piper name or path to a .onnx model.",
    )
    demo.add_argument("--live", action="store_true", help="Use the local Claude client.")
    demo.add_argument("--profile", default=None, help="model_config.yaml profile (with --live).")
    demo.set_defaults(func=_demo)

    gm = sub.add_parser(
        "genmusic", help="Turn a git repo's activity into a generative Strudel program."
    )
    gm.add_argument("--repo", required=True, help="Local path OR remote URL of a git repo.")
    gm.add_argument("--max-count", type=int, default=50, help="Commits to read.")
    gm.add_argument("--style", default="lofi", help="Generative style (M2: lofi).")
    gm.add_argument(
        "--base-intensity",
        type=float,
        default=THETA_START,
        help="User base energy 0..1; sessions start at theta (default).",
    )
    gm.add_argument(
        "--intensity",
        type=float,
        default=None,
        help="Override the activity-derived intensity (0..1).",
    )
    gm.add_argument("--out", default=None, help="Write the Strudel program to this file.")
    gm.set_defaults(func=_genmusic)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
