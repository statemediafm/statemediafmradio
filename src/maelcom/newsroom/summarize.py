"""Summarize a window of NewsItems into a radio-news ``Script``.

The pipeline is: render the items + a style brief into a prompt, send it through
the provider-neutral ``LLMClient``, and wrap the completion in a ``Script``. The
client and its config are injected, so tests drive this with ``FakeLLMClient``
and no network — and production defaults to the local Claude client via LiteLLM.
"""

from __future__ import annotations

from ..core.models import NewsItem, Script
from .llm import LLMClient, LLMConfig

# Rough words-per-minute for spoken radio, used to size the target read length.
_WORDS_PER_MINUTE = 150


def build_prompt(items: list[NewsItem], style: str, target_seconds: int = 90) -> str:
    """Render a NewsItem window + style brief into a summarization prompt.

    Pure and deterministic — no model call — so it can be asserted on directly.
    """
    target_words = max(40, round(target_seconds / 60 * _WORDS_PER_MINUTE))
    lines: list[str] = []
    for item in items:
        who = ", ".join(item.actors) if item.actors else "unknown"
        when = item.timestamp.isoformat() if item.timestamp else "recently"
        detail = f" — {item.body}" if item.body else ""
        lines.append(
            f"- [{item.source}/{item.kind}] {item.title}{detail} "
            f"(by {who}; {when})"
        )
    updates = "\n".join(lines)
    return (
        "You are a radio newsroom writer. Turn the following team updates into a "
        f"single spoken news segment of about {target_words} words "
        f"(~{target_seconds} seconds) in a {style} style. "
        "Cover who, what, where, when, why, and how. Write only the words to be "
        "read aloud — no headings, no stage directions, no bullet points.\n\n"
        f"Updates:\n{updates}"
    )


def naive_radio_script(items: list[NewsItem], style: str, target_seconds: int = 90) -> str:
    """A deterministic, LLM-free radio script built straight from the items.

    Real content derived from the actual activity — top contributors and recent
    headlines — so the zero-dependency demo (and its spoken audio) reflects the
    repository instead of a placeholder. Not as fluent as a model, but honest
    about the input and fully offline.
    """
    if not items:
        raise ValueError("naive_radio_script() requires at least one NewsItem")

    sources = sorted({it.source for it in items})
    desk = "/".join(sources) if sources else "news"

    counts: dict[str, int] = {}
    for it in items:
        for actor in it.actors:
            counts[actor] = counts.get(actor, 0) + 1
    top = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:3]
    if top:
        names = ", ".join(f"{name} ({n})" for name, n in top)
        contributors = f"Leading the activity: {names}. "
    else:
        contributors = ""

    headlines = [it.title.strip() for it in items[:5] if it.title.strip()]
    headline_text = " ".join(f"{h}." for h in headlines)

    return (
        f"You're listening to the {style} desk. "
        f"In the latest window, {len(items)} update"
        f"{'s' if len(items) != 1 else ''} came in from the {desk} desk. "
        f"{contributors}"
        f"Here are the headlines. {headline_text} "
        f"That's the recent activity. More as it develops."
    )


def summarize(
    items: list[NewsItem],
    style: str,
    *,
    client: LLMClient,
    cfg: LLMConfig,
    target_seconds: int = 90,
    voice: str | None = None,
) -> Script:
    """Turn ``items`` into a ``Script`` in the requested ``style``.

    ``client`` and ``cfg`` are required and injected — production wires the
    LiteLLM backend with the ``dev`` (local Claude) profile; tests pass
    ``FakeLLMClient``.
    """
    if not items:
        raise ValueError("summarize() requires at least one NewsItem")

    prompt = build_prompt(items, style, target_seconds=target_seconds)
    text = client.complete(prompt, cfg).strip()
    return Script(text=text, style=style, voice=voice)
