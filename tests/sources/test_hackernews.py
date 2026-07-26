"""Tests for the Hacker News front-page source, fully offline."""

from __future__ import annotations

from statemediafm.sources.hackernews import HackerNewsSource


class _FakeGet:
    def __init__(self, routes: dict[str, object]):
        self.routes = routes
        self.calls: list[str] = []

    def __call__(self, url: str):
        self.calls.append(url)
        for needle, payload in self.routes.items():
            if needle in url:
                return payload
        raise AssertionError(f"unexpected URL: {url}")


def _routes():
    return {
        "/topstories.json": [10, 20, 30],
        "/item/10.json": {
            "title": "Claude Opus 5",
            "by": "alvis",
            "score": 911,
            "descendants": 500,
            "url": "https://www.anthropic.com/news/x",
            "time": 1784912261,
            "type": "story",
        },
        "/item/20.json": {  # self-post: no url
            "title": "Ask HN: how do you test?",
            "by": "bob",
            "score": 5,
            "descendants": 0,
            "time": 1784900000,
            "type": "story",
        },
        "/item/30.json": None,  # dead/removed item → skipped
    }


def test_front_page_stories_map_to_news_items():
    fake = _FakeGet(_routes())
    items = HackerNewsSource(max_count=10, get=fake).poll()

    # The dead item (#30) is skipped.
    assert [i.title for i in items] == ["Claude Opus 5", "Ask HN: how do you test?"]

    top = items[0]
    assert top.kind == "story"
    assert top.source == "hackernews"
    assert top.actors == ["alvis"]
    assert top.id == "hn:10"
    assert top.origin == "Hacker News"
    assert top.raw["rank"] == 1
    # Body notes score, comments, and domain (www. stripped).
    assert "911 points and 500 comments" in top.body
    assert "anthropic.com" in top.body and "www." not in top.body
    assert top.refs == ["https://www.anthropic.com/news/x"]

    # A self-post falls back to its HN discussion link.
    selfpost = items[1]
    assert selfpost.refs == ["https://news.ycombinator.com/item?id=20"]
    assert "news.ycombinator.com" in selfpost.body


def test_max_count_limits_item_fetches():
    routes = {"/topstories.json": list(range(100))}
    routes["/item/0.json"] = {"title": "a", "by": "x", "score": 1, "descendants": 0, "time": 1}
    routes["/item/1.json"] = {"title": "b", "by": "y", "score": 2, "descendants": 0, "time": 2}
    fake = _FakeGet(routes)
    items = HackerNewsSource(max_count=2, get=fake).poll()
    assert len(items) == 2
    # Only the top 2 stories were fetched — no call for item #2.
    assert not any("/item/2.json" in c for c in fake.calls)
