"""Derive an ``ActivitySignal`` from a window of ``NewsItem``s (plan §5.3).

Pure and deterministic — the same items always yield the same signal — so the
generative-music composition downstream is golden-file testable. This is the
music pillar's ingest side: it never calls a model, it just reads features off
the activity stream.
"""

from __future__ import annotations

import re
from itertools import pairwise

from ..core.models import ActivitySignal, NewsItem

# Named voices/themes assigned to participants so the music can reflect *who* is
# active. Busiest participant gets the first voice.
_VOICE_PALETTE = (
    "rhodes",
    "upright-bass",
    "vibraphone",
    "felt-piano",
    "tape-flute",
    "dub-chord",
    "glockenspiel",
    "brushed-kit",
)

# Words too generic to be a "theme" — common English plus version-control verbs.
_STOPWORDS = frozenset(
    """
    the a an and or of to in for on with by from into onto is are was were be been
    add adds added fix fixes fixed update updates updated merge merged merges resolve
    resolves resolved remove removes removed use uses used make makes made bump refactor
    support release version wip test tests initial commit change changes work working
    this that these those it its via not now new
    """.split()  # noqa: SIM905 — readable prose block beats a long list literal
)


def _volatility(timestamps: list[float]) -> float:
    """Burstiness of arrival times as a 0..1 value.

    Evenly spaced events → low; clustered bursts → high. Uses the coefficient of
    variation of inter-arrival gaps, saturating at cv≈2. Needs ≥3 timed items;
    otherwise there is no meaningful spread and it returns 0.
    """
    ts = sorted(timestamps)
    if len(ts) < 3:
        return 0.0
    gaps = [b - a for a, b in pairwise(ts)]
    mean = sum(gaps) / len(gaps)
    if mean <= 0:
        return 1.0
    variance = sum((g - mean) ** 2 for g in gaps) / len(gaps)
    cv = variance**0.5 / mean
    return min(1.0, cv / 2.0)


def _themes(titles: list[str], limit: int = 5) -> list[str]:
    """Top content words across the item titles, most frequent first."""
    counts: dict[str, int] = {}
    for title in titles:
        for word in re.findall(r"[a-zA-Z][a-zA-Z0-9_-]{2,}", title.lower()):
            if word in _STOPWORDS:
                continue
            counts[word] = counts.get(word, 0) + 1
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return [word for word, _ in ranked[:limit]]


def activity(items: list[NewsItem], window_s: float | None = None) -> ActivitySignal:
    """Reduce a ``NewsItem`` window to an ``ActivitySignal``.

    ``window_s`` defaults to the span between the earliest and latest timestamped
    item (0 when fewer than two are timed). An empty window is valid: it yields a
    quiet, zero-volume signal so the music can idle at the base (theta) level.
    """
    # Rank participants by how many items they touched (busiest first).
    counts: dict[str, int] = {}
    for item in items:
        for actor in item.actors:
            counts[actor] = counts.get(actor, 0) + 1
    ranked_actors = [a for a, _ in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]
    actor_voices = {
        actor: _VOICE_PALETTE[i % len(_VOICE_PALETTE)] for i, actor in enumerate(ranked_actors)
    }

    epochs = [it.timestamp.timestamp() for it in items if it.timestamp is not None]
    if window_s is None:
        window_s = (max(epochs) - min(epochs)) if len(epochs) >= 2 else 0.0

    return ActivitySignal(
        window_s=window_s,
        volume=len(items),
        volatility=_volatility(epochs),
        participant_count=len(counts),
        themes=_themes([it.title for it in items]),
        actor_voices=actor_voices,
    )
