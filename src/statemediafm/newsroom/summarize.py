"""Summarize a window of NewsItems into a radio-news ``Script``.

The pipeline is: render the items + a style brief into a prompt, send it through
the provider-neutral ``LLMClient``, and wrap the completion in a ``Script``. The
client and its config are injected, so tests drive this with ``FakeLLMClient``
and no network — and production defaults to the local Claude client via LiteLLM.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import NamedTuple

from ..core.models import NewsItem, Script
from ..core.people import is_bot
from .llm import LLMClient, LLMConfig
from .personas import DEFAULT_IDENT, DEFAULT_SIGNOFF


class Read(NamedTuple):
    """One spoken chunk of a broadcast. ``origin`` (a source label) is set on
    ``headline`` reads so the voicer can pick a voice per source."""

    role: str  # "headline" | "other"
    text: str
    origin: str | None = None

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


def _origin(item: NewsItem) -> str:
    """Where a headline is from, for on-air attribution."""
    if item.origin:
        return item.origin
    return {"hackernews": "Hacker News"}.get(item.source, "the newsroom")


def _authored_by_bot(item: NewsItem) -> bool:
    """True if the item's author (its first actor) is an automation account."""
    return bool(item.actors) and is_bot(item.actors[0])


# Work-item kinds that carry a useful body (the latest comment or the description)
# worth speaking after the headline — see ``_forge_context``.
_FORGE_KINDS = frozenset({"issue", "pull_request", "merge_request"})
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")


def _speech_brief(text: str, max_words: int = 28) -> str:
    """A short, spoken-word-friendly gist of a work item's body: strip code
    fences, URLs, and markdown noise, then take the first sentence (capped to
    ``max_words``). ``""`` when there's nothing speakable."""
    t = re.sub(r"```.*?```", " ", text or "", flags=re.DOTALL)  # drop fenced code
    t = re.sub(r"https?://\S+", "", t)  # drop URLs (unreadable aloud)
    t = t.replace("`", "")
    t = re.sub(r"[*_#>|~\[\]]", " ", t)  # common markdown punctuation
    t = re.sub(r"\s+", " ", t).strip()
    if not t:
        return ""
    first = _SENTENCE_SPLIT.split(t, maxsplit=1)[0]
    words = first.split()
    if len(words) > max_words:
        first = " ".join(words[:max_words])
    return first.strip().rstrip(",;:")


def _forge_context(item: NewsItem) -> str:
    """A follow-up spoken sentence giving the substance of a forge work item —
    its latest comment or description — so the listener hears more than the title.
    ``""`` for non-forge items or ones with no usable body. When the body is a
    comment (``forge`` appends the commenter as a second actor), credit them."""
    if item.kind not in _FORGE_KINDS:
        return ""
    brief = _speech_brief(item.body or "")
    if not brief:
        return ""
    commenter = item.actors[1] if len(item.actors) >= 2 and not is_bot(item.actors[1]) else None
    return f"Latest, from {commenter}: {brief}" if commenter else brief


def time_greeting(now: datetime) -> str:
    """The broadcast's opening line, stating the current hour and minute."""
    return f"Good day. It is {now:%H:%M}."


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
        "Cover who, what, where, when, why, and how. Do not state a story's points "
        "or score; you may mention a comment count only when it is notably high. Put "
        "each distinct story or topic in its own paragraph, separated by a blank "
        "line. Write only the words to be read aloud — no headings, no stage "
        "directions, no bullet points.\n\n"
        f"Updates:\n{updates}"
    )


# Spoken pacing for LLM-written bulletins (multipliers on ``headline_pause_ms``):
# a short beat between sentences, a longer double beat between topics.
_SENTENCE_PAUSE = "0.45"
_TOPIC_PAUSE = "0.9"


def reads_from_prose(text: str) -> list[Read]:
    """Chunk an LLM-written bulletin into spoken ``Read``s with pacing: a pause
    between **sentences** and a longer double pause between **topics** (paragraphs,
    separated by a blank line — the prompt asks the model to break topics that way).
    The voicer (:func:`newsroom.tts.render_reads`) turns the pause markers into
    silence."""
    reads: list[Read] = []
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", (text or "").strip()) if p.strip()]
    for pi, para in enumerate(paragraphs):
        if pi:
            reads.append(Read("pause", "", _TOPIC_PAUSE))  # double pause between topics
        sentences = [s.strip() for s in _SENTENCE_SPLIT.split(para) if s.strip()]
        for si, sentence in enumerate(sentences):
            if si:
                reads.append(Read("pause", "", _SENTENCE_PAUSE))  # pause between sentences
            reads.append(Read("other", sentence))
    return reads or [Read("other", (text or "").strip())]


def bulletin_reads(text: str, *, ident: str | None = None, signoff: str | None = None) -> list[Read]:
    """Wrap an LLM-written bulletin with the station **ident** (opener) and
    **sign-off** (closer), pacing the prose in between (see :func:`reads_from_prose`).
    ``ident``/``signoff`` default to the firmwide phrasing; a persona overrides them.
    The sign-off is split after its first sentence with a double pause so it lands."""
    reads: list[Read] = [Read("other", ident or DEFAULT_IDENT), Read("pause", "", "2")]
    reads.extend(reads_from_prose(text))
    reads.append(Read("pause", "", "1"))  # a beat before the sign-off
    sign = signoff or DEFAULT_SIGNOFF
    head, sep, tail = sign.partition(". ")
    if sep and tail:
        reads.append(Read("other", head + "."))
        reads.append(Read("pause", "", "2"))
        reads.append(Read("other", tail))
    else:
        reads.append(Read("other", sign))
    return reads


def radio_reads(
    items: list[NewsItem],
    style: str,
    *,
    greeting: str | None = None,
    max_headlines: int = 5,
    ident: str | None = None,
    signoff: str | None = None,
) -> list[Read]:
    """The broadcast read as an ordered list of ``Read`` chunks.

    Splitting the read into chunks lets the voicer pause between headlines and
    switch voice per source (each headline carries its ``origin``);
    ``naive_radio_script`` is just these joined into one string, so the plain
    text is unchanged. ``max_headlines`` caps how many headlines each source
    contributes. Derived deterministically from the real activity.
    """
    if not items:
        raise ValueError("radio_reads() requires at least one NewsItem")

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

    # Credit people, not automation — bots dominate raw counts (dependabot,
    # codecov, …) and would otherwise headline the "who did the work" line.
    counts: dict[str, int] = {}
    for it in items:
        for actor in it.actors:
            if is_bot(actor):
                continue
            counts[actor] = counts.get(actor, 0) + 1
    top = [name for name, _ in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:3]]

    # Group headlines by origin and cover each source in full before the next
    # (depth-first) — no interleaving across sources. ``order`` preserves each
    # origin's first appearance; a per-source cap keeps one busy source from
    # crowding out the others. Deduped; bot-authored items skipped as noise.
    seen: set[str] = set()
    groups: dict[str, list[NewsItem]] = {}
    order: list[str] = []
    for it in items:
        if _authored_by_bot(it):
            continue
        headline = _clean_headline(it.title)
        if not headline or headline.lower() in seen:
            continue
        seen.add(headline.lower())
        origin = _origin(it)
        if origin not in groups:
            groups[origin] = []
            order.append(origin)
        if len(groups[origin]) < max_headlines:
            groups[origin].append(it)  # keep the item so forge context can follow
    single_origin = len(order) == 1

    plural = "s" if len(items) != 1 else ""
    reads: list[Read] = []
    if greeting:
        reads.append(Read("other", greeting))
    reads.append(Read("other", ident or DEFAULT_IDENT))
    reads.append(Read("pause", "", "2"))  # a double pause after the station ident
    volume_line = (
        f"In the latest update there {'were' if plural else 'was'} "
        f"{len(items)} item{plural}{across}."
    )
    reads.append(Read("other", volume_line))
    if top:
        reads.append(Read("other", f"Most of the activity came from {_join_names(top)}."))
    # Attribute the headlines. One source → name it once. Several → cover them
    # depth-first, announcing each source at the top of its run; every headline
    # read carries its origin so the voicer speaks each source in its own voice.
    if not order:
        reads.append(Read("other", "No standout headlines to report."))
    elif single_origin:
        origin = order[0]
        reads.append(Read("other", f"Here are the headlines from {origin}."))
        for it in groups[origin]:
            reads.append(Read("headline", f"{_clean_headline(it.title)}.", origin))
            ctx = _forge_context(it)
            if ctx:  # a forge item's latest comment / description, spoken after the title
                reads.append(Read("other", ctx))
    else:
        reads.append(Read("other", "Here are the headlines."))
        for origin in order:
            for i, it in enumerate(groups[origin]):
                h = _clean_headline(it.title)
                text = f"From {origin}, {h}." if i == 0 else f"{h}."
                reads.append(Read("headline", text, origin))
                ctx = _forge_context(it)
                if ctx:
                    reads.append(Read("other", ctx))
    # Double the pause after the last headline: the block already gets one beat
    # before the sign-off (headline→other); add one more.
    if order:
        reads.append(Read("pause", "", "1"))
    # Sign-off with a double pause after its first sentence ("And that's the current
    # state." … "More as things develop.") — a beat to let it land.
    sign = signoff or DEFAULT_SIGNOFF
    head, sep, tail = sign.partition(". ")
    if sep and tail:
        reads.append(Read("other", head + "."))
        reads.append(Read("pause", "", "2"))
        reads.append(Read("other", tail))
    else:
        reads.append(Read("other", sign))
    return reads


def naive_radio_script(
    items: list[NewsItem], style: str, *, greeting: str | None = None, max_headlines: int = 5
) -> str:
    """A deterministic, LLM-free radio script built straight from the items.

    Real content derived from the actual activity — top contributors and recent
    headlines — so the zero-dependency demo (and its spoken audio) reflects the
    activity instead of a placeholder. See ``radio_reads`` for the chunked form
    the voicer uses to pause between headlines.
    """
    reads = radio_reads(items, style, greeting=greeting, max_headlines=max_headlines)
    return " ".join(read.text for read in reads if read.role != "pause")


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
