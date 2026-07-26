"""End-to-end M1 slice test: NewsItems → plan, fully offline."""

from __future__ import annotations

from datetime import datetime

from statemediafm import pipeline
from statemediafm.core.models import NewsItem
from statemediafm.core.plan import plan_to_dict
from statemediafm.newsroom.llm import FakeLLMClient, LLMConfig
from statemediafm.newsroom.tts import ToneWavTTS


def _items():
    return [
        NewsItem(id="c1", source="git", kind="commit", title="Wire the scheduler",
                 actors=["alice"], timestamp=datetime(2026, 7, 24, 9, 0)),
    ]


def test_run_produces_single_news_segment():
    plan = pipeline.run(
        _items(),
        llm_client=FakeLLMClient(),
        llm_cfg=LLMConfig(model="fake/offline"),
        tts=ToneWavTTS(),
        style="bbc-world",
    )
    assert len(plan.segments) == 1
    seg = plan.segments[0]
    assert seg.kind == "news"
    assert seg.start_s == 0.0
    assert seg.duration_s > 0
    assert seg.audio is not None and seg.audio.data[:4] == b"RIFF"
    assert seg.script is not None and seg.script.style == "bbc-world"


def test_plan_to_dict_references_audio_by_url():
    plan = pipeline.run(
        _items(),
        llm_client=FakeLLMClient(),
        llm_cfg=LLMConfig(model="fake/offline"),
        tts=ToneWavTTS(),
    )
    d = plan_to_dict(plan)
    seg = d["segments"][0]
    assert seg["audio_url"] == f"/audio/{seg['audio_id']}"
    assert "script" in seg and seg["kind"] == "news"
