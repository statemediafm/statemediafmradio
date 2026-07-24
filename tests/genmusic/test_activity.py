"""Tests for deriving an ActivitySignal from a NewsItem window."""

from __future__ import annotations

from datetime import datetime, timedelta

from maelcom.core.models import ActivitySignal, NewsItem
from maelcom.genmusic.activity import activity


def _commit(id_: str, title: str, actor: str, when: datetime | None = None) -> NewsItem:
    return NewsItem(
        id=id_, source="git", kind="commit", title=title, actors=[actor], timestamp=when
    )


def test_counts_volume_and_participants():
    items = [
        _commit("c1", "Add scheduler", "alice"),
        _commit("c2", "Fix scheduler", "bob"),
        _commit("c3", "Tune scheduler", "alice"),
    ]
    signal = activity(items)
    assert isinstance(signal, ActivitySignal)
    assert signal.volume == 3
    assert signal.participant_count == 2


def test_empty_window_is_a_quiet_signal():
    signal = activity([])
    assert signal.volume == 0
    assert signal.participant_count == 0
    assert signal.volatility == 0.0
    assert signal.themes == []
    assert signal.actor_voices == {}


def test_bots_excluded_from_participant_count():
    items = [
        _commit("c1", "bump deps", "dependabot[bot]"),
        _commit("c2", "coverage", "codecov[bot]"),
        _commit("c3", "real work", "alice"),
    ]
    signal = activity(items)
    # Volume still counts every item, but only humans are participants.
    assert signal.volume == 3
    assert signal.participant_count == 1
    assert set(signal.actor_voices) == {"alice"}


def test_actor_voices_rank_busiest_first():
    items = [
        _commit("c1", "one", "heavy"),
        _commit("c2", "two", "heavy"),
        _commit("c3", "three", "light"),
    ]
    voices = activity(items).actor_voices
    # Busiest participant gets the first palette voice.
    assert voices["heavy"] == "rhodes"
    assert voices["light"] != voices["heavy"]


def test_themes_skip_stopwords_and_verbs():
    items = [
        _commit("c1", "Add telemetry to scheduler", "a"),
        _commit("c2", "Fix telemetry export", "b"),
        _commit("c3", "Update telemetry docs", "c"),
    ]
    themes = activity(items).themes
    # "telemetry" recurs and is content; "add/fix/update/to" are filtered out.
    assert "telemetry" in themes
    assert "add" not in themes and "fix" not in themes and "update" not in themes


def test_volatility_zero_when_too_few_timestamps():
    items = [_commit("c1", "x", "a", datetime(2026, 7, 24, 9, 0))]
    assert activity(items).volatility == 0.0


def test_volatility_higher_for_bursty_than_even_spacing():
    base = datetime(2026, 7, 24, 9, 0)
    even = [_commit(f"e{i}", "x", "a", base + timedelta(minutes=10 * i)) for i in range(6)]
    # Five tight together, then one far away → bursty.
    bursty_times = [0, 1, 2, 3, 4, 600]
    bursty = [_commit(f"b{i}", "x", "a", base + timedelta(minutes=m)) for i, m in enumerate(bursty_times)]
    assert activity(bursty).volatility > activity(even).volatility


def test_window_s_spans_timestamps():
    base = datetime(2026, 7, 24, 9, 0)
    items = [
        _commit("c1", "x", "a", base),
        _commit("c2", "y", "b", base + timedelta(seconds=90)),
    ]
    assert activity(items).window_s == 90.0


def test_activity_is_deterministic():
    items = [_commit("c1", "Add x", "a"), _commit("c2", "Fix y", "b")]
    assert activity(items) == activity(items)
