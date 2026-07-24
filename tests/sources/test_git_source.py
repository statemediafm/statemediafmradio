"""Tests for GitSource against a throwaway repository."""

from __future__ import annotations

import shutil
import subprocess

import pytest

from maelcom.sources import GitSource, get_source, is_remote


@pytest.mark.parametrize(
    "repo,expected",
    [
        ("https://gitlab.com/meltano/meltano", True),
        ("http://example.com/x.git", True),
        ("git://example.com/x.git", True),
        ("ssh://git@example.com/x.git", True),
        ("git@gitlab.com:meltano/meltano.git", True),
        ("/home/user/project", False),
        ("./relative/repo", False),
        ("../repo", False),
    ],
)
def test_is_remote_detection(repo, expected):
    assert is_remote(repo) is expected


def _git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


@pytest.fixture()
def repo(tmp_path):
    if shutil.which("git") is None:
        pytest.skip("git not installed")
    _git(tmp_path, "init")
    _git(tmp_path, "config", "user.email", "t@example.com")
    _git(tmp_path, "config", "user.name", "Tester")
    (tmp_path / "a.txt").write_text("hello")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-m", "Add greeting")
    (tmp_path / "a.txt").write_text("hello world")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-m", "Extend greeting")
    return tmp_path


def test_poll_returns_newest_first(repo):
    items = GitSource(str(repo)).poll()
    assert len(items) == 2
    assert items[0].title == "Extend greeting"  # git log is newest-first
    assert items[1].title == "Add greeting"


def test_item_fields_are_normalized(repo):
    item = GitSource(str(repo)).poll()[0]
    assert item.source == "git"
    assert item.kind == "commit"
    assert item.actors == ["Tester"]
    assert item.timestamp is not None
    assert item.id and item.refs == [item.id]


def test_git_source_is_registered():
    assert get_source("git") is GitSource
