"""Tests for the newsroom summarizer, driven by the offline FakeLLMClient.

No network, no credentials — the fake client makes the summarization pipeline
deterministic end to end.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from statemediafm.core.models import NewsItem, Script
from statemediafm.core.people import is_bot
from statemediafm.newsroom.llm import FakeLLMClient, LLMConfig
from statemediafm.newsroom.summarize import (
    build_prompt,
    naive_radio_script,
    radio_reads,
    summarize,
    time_greeting,
)

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
    # Firmwide framing, not a "bbc-world desk".
    assert "firmwide radio service" in text
    assert "desk" not in text
    assert "2 items" in text
    # Real content: contributors and headlines come from the items, not a stub.
    assert "alice" in text
    assert "Fix scheduler offset bug" in text
    assert "Deploy window moved to 14:00" in text
    # It's a plain spoken script — no prompt scaffolding leaks in.
    assert "who, what, where" not in text
    assert "[fake:" not in text


def test_naive_radio_script_names_issue_and_mr_kinds():
    items = [
        NewsItem(id="i1", source="forge", kind="issue", title="Scheduler hangs", actors=["a"]),
        NewsItem(id="p1", source="forge", kind="pull_request", title="Fix the hang", actors=["b"]),
    ]
    text = naive_radio_script(items, style="bbc-world")
    assert "issues" in text and "pull requests" in text


def test_naive_radio_script_pluralizes_hn_stories():
    items = [NewsItem(id="hn:1", source="hackernews", kind="story", title="Big news", actors=["a"])]
    assert "stories" in naive_radio_script(items, style="bbc-world")


def test_headlines_attributed_to_a_single_origin():
    items = [
        NewsItem(id="hn:1", source="hackernews", kind="story", title="Claude Opus 5",
                 origin="Hacker News", actors=["a"]),
        NewsItem(id="hn:2", source="hackernews", kind="story", title="Postgres scales",
                 origin="Hacker News", actors=["b"]),
    ]
    text = naive_radio_script(items, style="bbc-world")
    assert "Here are the headlines from Hacker News." in text
    assert "Claude Opus 5." in text


def test_headlines_attributed_per_source_when_mixed():
    items = [
        NewsItem(id="hn:1", source="hackernews", kind="story", title="Opus 5",
                 origin="Hacker News", actors=["a"]),
        NewsItem(id="c1", source="git", kind="commit", title="Fix the bug",
                 origin="meltano", actors=["b"]),
    ]
    text = naive_radio_script(items, style="bbc-world")
    assert "From Hacker News, Opus 5." in text
    assert "From meltano, Fix the bug." in text


def test_max_headlines_caps_per_source():
    items = [
        NewsItem(id=f"h{i}", source="hackernews", kind="story", title=f"Story {i}",
                 origin="Hacker News", actors=["a"])
        for i in range(12)
    ]
    five = [r for r in radio_reads(items, "bbc-world") if r.role == "headline"]
    ten = [r for r in radio_reads(items, "bbc-world", max_headlines=10) if r.role == "headline"]
    assert len(five) == 5  # default
    assert len(ten) == 10


def test_multi_source_headlines_are_grouped_depth_first():
    # Interleaved input, but headlines must come out grouped by source.
    items = [
        NewsItem(id="h1", source="hackernews", kind="story", title="HN one",
                 origin="Hacker News", actors=["a"]),
        NewsItem(id="r1", source="git", kind="commit", title="Repo one", origin="proj", actors=["b"]),
        NewsItem(id="h2", source="hackernews", kind="story", title="HN two",
                 origin="Hacker News", actors=["c"]),
        NewsItem(id="r2", source="git", kind="commit", title="Repo two", origin="proj", actors=["d"]),
    ]
    headlines = [(r.text, r.origin) for r in radio_reads(items, "bbc-world") if r.role == "headline"]
    # All Hacker News headlines precede all repo headlines (no interleaving).
    assert [o for _, o in headlines] == ["Hacker News", "Hacker News", "proj", "proj"]
    # Each source is announced at the top of its run.
    assert headlines[0][0] == "From Hacker News, HN one."
    assert headlines[2][0] == "From proj, Repo one."


def test_time_greeting_states_hour_and_minute():
    assert time_greeting(datetime(2026, 7, 24, 16, 52)) == "Good day. It is 16:52."
    assert time_greeting(datetime(2026, 7, 24, 9, 5)) == "Good day. It is 09:05."


def test_radio_reads_join_equals_script():
    items = _items()
    reads = radio_reads(items, "bbc-world")
    voiced = " ".join(r.text for r in reads if r.role != "pause")  # pauses carry no text
    assert voiced == naive_radio_script(items, "bbc-world")


def test_radio_reads_marks_headlines_and_greeting():
    reads = radio_reads(_items(), "bbc-world", greeting="Good day. It is 09:00.")
    # The greeting is the first read.
    assert reads[0].role == "other"
    assert reads[0].text == "Good day. It is 09:00."
    # Each unique headline is its own 'headline' read.
    headlines = [r.text for r in reads if r.role == "headline"]
    assert "Fix scheduler offset bug." in headlines
    assert "Deploy window moved to 14:00." in headlines


def test_radio_reads_uses_persona_ident_and_signoff():
    reads = radio_reads(_items(), "bbc-world",
                        ident="This is the world service.",
                        signoff="Do stay with us.")
    texts = [r.text for r in reads]
    assert "This is the world service." in texts
    assert texts[-1] == "Do stay with us."
    # The default firmwide phrasing is replaced, not appended.
    assert "This is the firmwide radio service." not in texts


def test_radio_reads_defaults_to_firmwide_phrasing():
    reads = radio_reads(_items(), "bbc-world")
    texts = [r.text for r in reads]
    assert "This is the firmwide radio service." in texts
    # the sign-off is split by a double pause after its first sentence
    assert "And that's the current state." in texts
    i = texts.index("And that's the current state.")
    assert reads[i + 1].role == "pause" and reads[i + 1].origin == "2"  # double pause
    assert reads[i + 2].text == "More as things develop." and texts[-1] == "More as things develop."


def test_is_bot_detects_automation_but_not_people():
    for bot in ["dependabot[bot]", "codecov[bot]", "github-actions[bot]", "renovate",
                "snyk-bot", "pre-commit_ci_bot", "MeltyBot"]:
        assert is_bot(bot)
    for person in ["alice", "edgarrmondragon", "Jamie Reid", "Abbott"]:
        assert not is_bot(person)


def test_contributors_line_excludes_bots():
    items = [
        NewsItem(id="1", source="forge", kind="pull_request", title="bump nox",
                 actors=["dependabot[bot]"]),
        NewsItem(id="2", source="forge", kind="pull_request", title="coverage",
                 actors=["codecov[bot]"]),
        NewsItem(id="3", source="forge", kind="pull_request", title="real fix",
                 actors=["alice"]),
    ]
    text = naive_radio_script(items, "bbc-world")
    assert "dependabot" not in text
    assert "codecov" not in text
    assert "Most of the activity came from alice." in text


def test_contributors_line_dropped_when_only_bots():
    items = [
        NewsItem(id="1", source="forge", kind="pull_request", title="bump",
                 actors=["dependabot[bot]"]),
    ]
    text = naive_radio_script(items, "bbc-world")
    assert "Most of the activity came from" not in text  # no humans → no credit line


def test_bot_authored_headlines_are_suppressed():
    items = [
        NewsItem(id="1", source="forge", kind="pull_request", title="bump nox from 1 to 2",
                 actors=["dependabot[bot]"]),
        NewsItem(id="2", source="forge", kind="pull_request", title="Add real feature",
                 actors=["alice"]),
    ]
    headlines = [r.text for r in radio_reads(items, "bbc-world") if r.role == "headline"]
    assert any("Add real feature" in h for h in headlines)
    assert not any("bump nox" in h for h in headlines)


def test_all_bot_authored_reports_no_headlines():
    items = [
        NewsItem(id="1", source="forge", kind="pull_request", title="bump x",
                 actors=["renovate[bot]"]),
    ]
    text = naive_radio_script(items, "bbc-world")
    assert "No standout headlines to report." in text
    assert "bump x" not in text


def test_radio_reads_headlines_carry_origin():
    items = [
        NewsItem(id="hn:1", source="hackernews", kind="story", title="Opus 5",
                 origin="Hacker News", actors=["a"]),
        NewsItem(id="c1", source="git", kind="commit", title="Fix the bug",
                 origin="meltano", actors=["b"]),
    ]
    reads = radio_reads(items, "bbc-world")
    by_origin = {r.origin for r in reads if r.role == "headline"}
    assert by_origin == {"Hacker News", "meltano"}


def test_naive_radio_script_greeting_is_prepended():
    text = naive_radio_script(_items(), "bbc-world", greeting="Good day. It is 09:00.")
    assert text.startswith("Good day. It is 09:00. This is the firmwide radio service.")


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
    assert "was 1 item" in naive_radio_script(one, style="lofi")
