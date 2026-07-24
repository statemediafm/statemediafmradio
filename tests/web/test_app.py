"""Web-layer tests. Skipped unless the [web] extra (FastAPI + httpx) is present."""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

from maelcom.core.models import ActivitySignal
from maelcom.genmusic.compose import compose
from maelcom.web.app import _State, create_app


def test_health_ok():
    client = TestClient(create_app())
    assert client.get("/health").json() == {"status": "ok"}


def test_genmusic_empty_then_published():
    state = _State()
    client = TestClient(create_app(state))
    # Nothing published yet.
    assert client.get("/genmusic").json() == {"text": None}

    program = compose(
        ActivitySignal(window_s=0.0, volume=5, volatility=0.3, participant_count=2)
    )
    state.set_program(program)
    body = client.get("/genmusic").json()
    assert body["style"] == "lofi"
    assert body["brainwave_band"] == program.brainwave_band
    assert "stack(" in body["text"]
    assert body["fade_ms"] == 2000
