"""Tests for the newsroom summarizer, driven by the offline FakeLLMClient.

No network, no credentials — the fake client makes the summarization pipeline
deterministic end to end.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from maelcom.core.models import NewsItem, Script
from maelcom.newsroom.llm import FakeLLMClient, LLMConfig
from maelcom.newsroom.summarize import build_prompt, naive_radio_script, summarize

CFG = LLMConfig(model="test-model")


def _items() -> list[NewsItem]:
    return [
        NewsItem(
            id="c1",
            source="git",
            kind="commit",
            title="Fix scheduler offset bug",
            body="Corrected the -9m offset applied to the news cadence.",
            actors=["alice"],
            timestamp=datetime(2026, 7, 24, 9, 15),
        ),
        NewsItem(
            id="s2",
            source="slack",
            kind="message",
            title="Deploy window moved to 14:00",
            actors=["bob", "carol"],
        ),
    ]


def test_build_prompt_includes_items_and_style():
    prompt = build_prompt(_items(), style="bbc-world", target_seconds=90)
    assert "bbc-world" in prompt
    # who/what/where/when/why/how brief is present
    assert "who, what, where, when, why, and how" in prompt
    # each item's source, title, and actors are rendered
    assert "[git/commit] Fix scheduler offset bug" in prompt
    assert "[slack/message] Deploy window moved to 14:00" in prompt
    assert "by alice" in prompt
    assert "by bob, carol" in prompt
    # actorless / timestampless items degrade gracefully
    assert "recently" in prompt  # s2 has no timestamp


def test_target_seconds_scales_word_budget():
    short = build_prompt(_items(), style="lofi", target_seconds=60)
    long = build_prompt(_items(), style="lofi", target_seconds=120)
    assert "about 150 words" in short
    assert "about 300 words" in long


def test_summarize_returns_script_via_fake_client():
    script = summarize(_items(), style="bbc-world", client=FakeLLMClient(), cfg=CFG)
    assert isinstance(script, Script)
    assert script.style == "bbc-world"
    assert script.voice is None
    assert script.segments == []
    # FakeLLMClient tags output with the configured model → proves it was used
    assert script.text.startswith("[fake:test-model]")


def test_summarize_passes_voice_through():
    script = summarize(
        _items(), style="john-peel", client=FakeLLMClient(), cfg=CFG, voice="peel"
    )
    assert script.voice == "peel"


def test_summarize_rejects_empty_window():
    with pytest.raises(ValueError):
        summarize([], style="lofi", client=FakeLLMClient(), cfg=CFG)


def test_naive_radio_script_reflects_real_items():
    text = naive_radio_script(_items(), style="bbc-world")
    # Style and item count are announced.
    assert "bbc-world" in text
    assert "2 changes" in text
    # Real content: contributors and headlines come from the items, not a stub.
    assert "alice" in text
    assert "Fix scheduler offset bug" in text
    assert "Deploy window moved to 14:00" in text
    # It's a plain spoken script — no prompt scaffolding leaks in.
    assert "who, what, where" not in text
    assert "[fake:" not in text


def test_naive_radio_script_is_deterministic():
    assert naive_radio_script(_items(), "lofi") == naive_radio_script(_items(), "lofi")


def test_naive_radio_script_ranks_top_contributors_conversationally():
    many = [
        NewsItem(id=f"c{i}", source="git", kind="commit", title=f"t{i}", actors=["heavy"])
        for i in range(5)
    ] + [NewsItem(id="c9", source="git", kind="commit", title="t9", actors=["light"])]
    text = naive_radio_script(many, style="lofi")
    # Names appear, but not the raw counts — that's metadata, not speech.
    assert "heavy" in text
    assert "(5)" not in text
    # Top contributor precedes the lighter one.
    assert text.index("heavy") < text.index("light")


def test_naive_radio_script_strips_non_conversational_metadata():
    items = [
        NewsItem(
            id="c1",
            source="git",
            kind="commit",
            title="Merged: Fix the `env:` bug (MR meltano/meltano!2665)",
            actors=["dev"],
        ),
        # Duplicate of the merge's underlying commit — should not repeat.
        NewsItem(id="c2", source="git", kind="commit", title="Fix the `env:` bug", actors=["dev"]),
    ]
    text = naive_radio_script(items, style="lofi")
    assert "!" not in text  # no exclamation points at all
    assert "MR meltano" not in text  # tracker reference dropped
    assert "Merged:" not in text  # merge prefix dropped
    assert "`" not in text  # code formatting dropped
    assert text.count("Fix the env: bug") == 1  # deduped to a single headline


def test_naive_radio_script_rejects_empty_window():
    with pytest.raises(ValueError):
        naive_radio_script([], style="lofi")


def test_naive_radio_script_singular_update_wording():
    one = [NewsItem(id="c1", source="git", kind="commit", title="only", actors=["a"])]
    assert "was 1 change" in naive_radio_script(one, style="lofi")
