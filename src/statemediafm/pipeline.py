"""M1 vertical slice, wired end to end.

NewsItems → summarize (LLMClient) → voice (TTSProvider) → single-segment
BroadcastPlan. Every dependency is injected, so the whole slice runs offline
with the fake LLM client + tone TTS, and switches to the local Claude client by
swapping the injected client — no code change.
"""

from __future__ import annotations

from .core.models import BroadcastPlan, NewsItem
from .core.plan import single_news_plan
from .newsroom.llm import LLMClient, LLMConfig
from .newsroom.summarize import summarize
from .newsroom.tts import TTSProvider


def run(
    items: list[NewsItem],
    *,
    llm_client: LLMClient,
    llm_cfg: LLMConfig,
    tts: TTSProvider,
    style: str = "bbc-world",
    voice: str | None = None,
    tenant: str | None = None,
) -> BroadcastPlan:
    """Summarize ``items``, voice the script, and return a one-segment plan."""
    script = summarize(items, style, client=llm_client, cfg=llm_cfg, voice=voice)
    audio = tts.render(script, voice=voice)
    return single_news_plan(audio, script, tenant=tenant)
