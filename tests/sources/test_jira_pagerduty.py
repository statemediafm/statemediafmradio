"""Offline tests for the Jira and PagerDuty sources (injectable getters)."""

from __future__ import annotations

from maelcom.sources.jira import JiraSource
from maelcom.sources.pagerduty import PagerDutySource


def test_jira_reads_project_issues():
    def get(url):
        assert "project" in url and "OPS" in url and "/rest/api/3/search" in url
        return {
            "issues": [
                {
                    "key": "OPS-12",
                    "fields": {
                        "summary": "Fix the login bug",
                        "status": {"name": "In Progress"},
                        "issuetype": {"name": "Bug"},
                        "assignee": {"displayName": "Ada"},
                        "updated": "2024-01-15T10:30:00.000+00:00",
                    },
                },
                {"key": "OPS-13", "fields": {"summary": "   "}},  # empty summary → skipped
            ]
        }

    src = JiraSource("OPS", token="me@x.com:apitok", endpoint="https://x.atlassian.net", get=get)
    items = src.poll()
    assert len(items) == 1
    it = items[0]
    assert it.source == "jira" and it.kind == "issue" and it.id == "jira:OPS-12"
    assert it.title == "Fix the login bug" and it.origin == "Jira OPS"
    assert it.actors == ["Ada"] and "Bug OPS-12" in it.body and "In Progress" in it.body
    assert it.timestamp is not None


def test_jira_needs_token_and_endpoint():
    assert JiraSource("OPS", token=None, endpoint="https://x", get=lambda u: {}).poll() == []
    assert JiraSource("OPS", token="t", endpoint="", get=lambda u: {}).poll() == []


def test_pagerduty_reads_incidents():
    def get(url):
        assert "/incidents?" in url and "statuses%5B%5D=triggered" in url
        return {
            "incidents": [
                {
                    "id": "PABC",
                    "title": "API latency spike",
                    "status": "triggered",
                    "urgency": "high",
                    "service": {"summary": "Checkout"},
                    "assignments": [{"assignee": {"summary": "Bob"}}],
                    "created_at": "2024-02-01T09:00:00Z",
                },
                {"id": "PXYZ", "title": "  "},  # empty → skipped
            ]
        }

    src = PagerDutySource(token="pdtok", get=get)
    items = src.poll()
    assert len(items) == 1
    it = items[0]
    assert it.source == "pagerduty" and it.kind == "incident" and it.id == "pd:PABC"
    assert it.title == "API latency spike" and it.origin == "PagerDuty"
    assert it.actors == ["Bob"]
    assert "high urgency" in it.body and "triggered" in it.body and "on Checkout" in it.body
    assert it.timestamp is not None


def test_pagerduty_needs_token():
    assert PagerDutySource(token=None, get=lambda u: {}).poll() == []


def test_roster_builds_jira_and_pagerduty():
    from maelcom.roster import build_roster

    roster = build_roster(
        {
            "segments": [
                {"topic": "Incidents", "source": "pagerduty"},
                {"topic": "Backlog", "source": "jira", "project": "OPS"},
            ]
        }
    )
    assert isinstance(roster[0][1], PagerDutySource)
    assert isinstance(roster[1][1], JiraSource) and roster[1][1].project == "OPS"
