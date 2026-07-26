"""Tests for the rhythm-of-the-day Director (pure, no clock)."""

from __future__ import annotations

from statemediafm.core.director import FELT_MAX_GAP_S, Director
from statemediafm.core.schedule import Cadence


def test_news_bulletins_on_the_17_minute_cadence():
    d = Director()  # default 17-minute news cadence
    news = [c.at_s for c in d.running_order(3600.0) if c.kind == "news"]
    assert news == [0.0, 17 * 60, 34 * 60, 51 * 60]


def test_song_slots_land_between_bulletins():
    d = Director()  # song every 20m, offset 10m
    songs = [c.at_s for c in d.running_order(3600.0) if c.kind == "song"]
    assert songs == [10 * 60, 30 * 60, 50 * 60]
    assert all(c.cue is not None for c in d.running_order(3600.0) if c.kind == "song")


def test_felt_cadence_never_exceeds_the_max_gap():
    d = Director()
    order = d.running_order(3600.0)
    times = [0.0] + [c.at_s for c in order] + [3600.0]
    gaps = [times[i + 1] - times[i] for i in range(len(times) - 1)]
    assert max(gaps) <= FELT_MAX_GAP_S + 1e-6  # idents bridge every long gap
    assert any(c.kind == "ident" for c in order)  # ...and some were needed


def test_idents_are_evenly_spaced_no_stragglers():
    # A wide custom gap: news every 30m, no song, 5-min felt cap → even 5-min idents.
    d = Director(news=Cadence(30 * 60), song=Cadence(1e9))
    order = d.running_order(30 * 60)
    idents = [c.at_s for c in order if c.kind == "ident"]
    assert idents == [5 * 60, 10 * 60, 15 * 60, 20 * 60, 25 * 60]


def test_due_cues_window_is_half_open_on_the_right():
    d = Director()
    # First tick from -1: the opening bulletin at t=0 is due.
    assert d.news_due(-1.0, 0.0) is True
    # A window that steps over the 17-min slot fires exactly once.
    assert d.news_due(16 * 60, 18 * 60) is True
    assert d.news_due(1 * 60, 5 * 60) is False  # nothing between minute 1 and 5
    # Boundary: the slot at 1020 is due when now hits it, not before.
    assert d.news_due(1000.0, 1020.0) is True
    assert d.news_due(1000.0, 1019.0) is False


def test_next_cue_after_now():
    d = Director()
    nxt = d.next_cue(0.0)  # first foreground after t=0 is the 10-min song
    assert nxt.kind == "song" and nxt.at_s == 10 * 60
    assert d.next_cue(11 * 60).at_s == 17 * 60  # then the 17-min bulletin


def test_custom_news_cadence():
    d = Director(news=Cadence(10 * 60))
    news = [c.at_s for c in d.running_order(1800.0) if c.kind == "news"]
    assert news == [0.0, 600.0, 1200.0]
