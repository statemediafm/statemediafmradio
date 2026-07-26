"""Offline tests for the Slack source (injectable HTTP getter)."""

from __future__ import annotations

from statemediafm.sources.slack import SlackSource, _clean


def test_clean_slack_markup():
    assert _clean("Shipped the <https://x.com/y|thing>!") == "Shipped the thing!"
    assert _clean("see <https://x.com>") == "see https://x.com"
    assert _clean("hi <@U123> in <#C1|general> &amp; done") == "hi in general & done"


def _fake_get(messages, channels=None, user=None):
    channels = channels if channels is not None else [{"id": "C1", "name": "eng"}]
    user = user or {"profile": {"display_name": "Ada"}}

    def get(url):
        if "conversations.list" in url:
            return {"ok": True, "channels": channels}
        if "conversations.history" in url:
            return {"ok": True, "messages": messages}
        if "users.info" in url:
            return {"ok": True, "user": user}
        return {}

    return get


def test_slack_reads_channel_and_skips_bots_and_joins():
    msgs = [
        {"user": "U1", "ts": "1700000000.0001", "text": "Shipped the <https://x|thing>!"},
        {"subtype": "channel_join", "user": "U2", "ts": "1700000001.0", "text": "joined"},
        {"bot_id": "B1", "ts": "1700000002.0", "text": "bot noise"},
        {"user": "U1", "ts": "1700000003.0", "text": "   "},  # empty after clean
    ]
    src = SlackSource("eng", token="xoxb-test", get=_fake_get(msgs))
    items = src.poll()
    assert len(items) == 1  # only the one real, non-empty message
    it = items[0]
    assert it.source == "slack" and it.kind == "message" and it.origin == "#eng"
    assert it.title == "Shipped the thing!"
    assert it.actors == ["Ada"]
    assert it.id == "slack:C1:1700000000.0001"
    assert it.timestamp is not None


def test_slack_accepts_a_channel_id_without_a_lookup():
    seen = {"listed": False}

    def get(url):
        if "conversations.list" in url:
            seen["listed"] = True
        if "conversations.history" in url:
            return {"ok": True, "messages": [{"user": "U1", "ts": "1.0", "text": "hi <@U2> there"}]}
        if "users.info" in url:
            return {"ok": True, "user": {"real_name": "Bob"}}
        return {}

    src = SlackSource("C0DEADBEEF", token="t", get=get)
    items = src.poll()
    assert seen["listed"] is False  # a raw ID needs no channel lookup
    assert items[0].title == "hi there"  # mention dropped
    assert items[0].actors == ["Bob"]


def test_slack_no_token_returns_empty():
    assert SlackSource("eng", token=None, get=lambda u: {}).poll() == []


def test_slack_unknown_channel_returns_empty():
    src = SlackSource("missing", token="t", get=_fake_get([], channels=[{"id": "C1", "name": "eng"}]))
    assert src.poll() == []


def test_roster_builds_slack_source():
    from statemediafm.roster import build_roster

    roster = build_roster({"segments": [{"topic": "Eng", "source": "slack", "channel": "eng"}]})
    assert roster[0][0] == "Eng" and isinstance(roster[0][1], SlackSource)
