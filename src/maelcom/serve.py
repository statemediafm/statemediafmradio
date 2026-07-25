"""``maelcom serve``: the web server plus a background refresh loop.

The loop periodically polls the roster's sources and republishes two things to
the app state: the generative-music program (``/genmusic``) and the voiced news
plan (``/plan``). Music is recomputed every tick (cheap, deterministic); the
news plan is only re-voiced when the item set actually changes, so TTS isn't run
on every tick.

``refresh_once`` is a plain function (no web/async deps) so it is unit-testable
offline; ``run`` wires it into a uvicorn server via an async task.
"""

from __future__ import annotations

import sys

from .core.models import Script
from .core.schedule import Programme, assemble_broadcast
from .genmusic import activity, compose
from .newsroom.summarize import radio_reads
from .newsroom.tts import TTSProvider, render_reads


def refresh_once(
    state,
    roster: list,
    tts: TTSProvider,
    *,
    cache: dict,
    style: str = "bbc-world",
    headline_pause_ms: int = 1000,
) -> None:
    """One refresh: recompute the music program, and the news plan if changed.

    ``roster`` is ``(topic, source, cadence, headlines)`` entries. Sources that
    error or return nothing are skipped. ``cache`` (a dict owned by the caller)
    holds the last item-set signature so the news plan is only re-voiced when the
    activity actually changed.
    """
    per_topic: list[tuple] = []
    all_items: list = []
    for topic, source, cadence, headlines in roster:
        try:
            items = source.poll()
        except OSError:  # network / API failure — skip this source this tick
            continue
        if not items:
            continue
        per_topic.append((topic, items, cadence, headlines))
        all_items.extend(items)

    if not all_items:
        return

    # Music every tick — cheap and deterministic from the current activity. Use
    # the currently-selected ambient generator; remember the signal so a live
    # model switch can recompose immediately.
    signal = activity(all_items)
    state.last_signal = signal
    state.set_program(compose(signal, style=getattr(state, "model", "Entrainment 0.1")))

    # News plan only when the item set changed (voicing is the expensive part).
    signature = tuple(sorted(item.id for item in all_items))
    if cache.get("news_sig") == signature:
        return
    cache["news_sig"] = signature

    programmes: list[Programme] = []
    content: dict = {}
    for topic, items, cadence, headlines in per_topic:
        reads = radio_reads(items, style, max_headlines=headlines or 5)
        script = Script(text=" ".join(r.text for r in reads), style=style)
        audio = render_reads(reads, tts, style=style, headline_pause_ms=headline_pause_ms)
        content[topic] = (script, audio)
        programmes.append(Programme(topic, cadence))
    state.set_plan(assemble_broadcast(programmes, content, window_s=3600))


def run(
    roster: list,
    tts: TTSProvider,
    *,
    host: str = "127.0.0.1",
    port: int = 8000,
    refresh: float = 60.0,
    headline_pause_ms: int = 1000,
    style: str = "bbc-world",
) -> int:
    """Boot the FastAPI app and drive ``refresh_once`` on an interval."""
    try:
        import asyncio

        import uvicorn

        from .web.app import _State, create_app
    except ImportError as exc:
        raise SystemExit("serve needs the [web] extra: pip install -e '.[web]'") from exc

    state = _State()
    app = create_app(state)
    cache: dict = {}

    async def _loop() -> None:
        while True:
            try:
                await asyncio.to_thread(
                    refresh_once,
                    state,
                    roster,
                    tts,
                    cache=cache,
                    style=style,
                    headline_pause_ms=headline_pause_ms,
                )
            except Exception as exc:  # noqa: BLE001 — one bad tick must not kill the loop
                print(f"refresh error: {exc}", file=sys.stderr)
            await asyncio.sleep(refresh)

    print(
        f"maelcom serving on http://{host}:{port}  (refreshing every {refresh:.0f}s)",
        file=sys.stderr,
    )

    # Run the refresh loop as a task in the same event loop as the server.
    config = uvicorn.Config(app, host=host, port=port, log_level="warning")
    server = uvicorn.Server(config)

    async def _main() -> None:
        task = asyncio.create_task(_loop())
        try:
            await server.serve()
        finally:
            task.cancel()

    asyncio.run(_main())
    return 0
