"""Summarize a window of NewsItems into a radio-news ``Script``.

The pipeline is: render the items + a style brief into a prompt, send it through
the provider-neutral ``LLMClient``, and wrap the completion in a ``Script``. The
client and its config are injected, so tests drive this with ``FakeLLMClient``
and no network — and production defaults to the local Claude client via LiteLLM.
"""

from __future__ import annotations

import re

from ..core.models import NewsItem, Script
from .llm import LLMClient, LLMConfig

# Rough words-per-minute for spoken radio, used to size the target read length.
_WORDS_PER_MINUTE = 150

# Trailing issue/MR/PR references, e.g. "(MR meltano/meltano!2665)", "(#123)",
# "(GH-99)" — metadata that reads as noise when spoken aloud.
_TRAILING_REF = re.compile(r"\s*\((?:MR|PR|GH|GL|#)[^)]*\)\s*$", re.IGNORECASE)
_MERGE_PREFIX = re.compile(r"^merged:\s*", re.IGNORECASE)


def _clean_headline(title: str) -> str:
    """Turn a raw commit subject into a spoken-word-friendly headline.

    Drops trailing MR/issue references, the ``Merged:`` prefix, backtick code
    formatting, and exclamation points, then collapses whitespace — so the
    result is plain conversational English with no tracker metadata.
    """
    text = _TRAILING_REF.sub("", title.strip())
    text = _MERGE_PREFIX.sub("", text)
    text = text.replace("`", "").replace("!", "")
    text = re.sub(r"\s+", " ", text).strip()
    return text.rstrip(".")


def _join_names(names: list[str]) -> str:
    """Join names as natural speech: 'a', 'a and b', 'a, b, and c'."""
    if len(names) == 1:
        return names[0]
    if len(names) == 2:
        return f"{names[0]} and {names[1]}"
    return f"{', '.join(names[:-1])}, and {names[-1]}"


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

    # Describe the mix of item kinds (issues, merge/pull requests, commits, …).
    _kind_noun = {
        "issue": "issues",
        "pull_request": "pull requests",
        "merge_request": "merge requests",
        "commit": "commits",
        "story": "stories",
    }
    kinds = sorted({it.kind for it in items})
    kind_nouns = [_kind_noun.get(k, f"{k}s") for k in kinds]
    across = f" across {_join_names(kind_nouns)}" if kind_nouns else ""

    counts: dict[str, int] = {}
    for it in items:
        for actor in it.actors:
            counts[actor] = counts.get(actor, 0) + 1
    top = [name for name, _ in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:3]]
    contributors = f"Most of the activity came from {_join_names(top)}. " if top else ""

    # Clean each subject, then keep the first few unique headlines — the merge /
    # underlying-commit pairing in many repos otherwise repeats the same line.
    seen: set[str] = set()
    headlines: list[str] = []
    for it in items:
        headline = _clean_headline(it.title)
        if headline and headline.lower() not in seen:
            seen.add(headline.lower())
            headlines.append(headline)
        if len(headlines) == 5:
            break
    headline_text = " ".join(f"{h}." for h in headlines)

    plural = "s" if len(items) != 1 else ""
    return (
        "This is the firmwide radio service. "
        f"In the latest update there {'were' if plural else 'was'} "
        f"{len(items)} item{plural}{across}. "
        f"{contributors}"
        f"Here are the headlines. {headline_text} "
        f"That's the latest from the newsroom. More as it develops."
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
