"""Tests for LLM timeout/failure fallback in citation intent classification."""

import asyncio

import pytest

from app.models.schemas import CitationIntent, Paper
from app.services.ai_classifier import SmartCitationClassifier


class _DummyCompletionsSleep:
    def __init__(self, sleep_seconds: float):
        self._sleep_seconds = sleep_seconds

    async def create(self, *args, **kwargs):
        await asyncio.sleep(self._sleep_seconds)
        raise AssertionError("Expected asyncio.wait_for timeout")


class _DummyCompletionsError:
    async def create(self, *args, **kwargs):
        raise RuntimeError("boom")


class _DummyChat:
    def __init__(self, completions):
        self.completions = completions


class _DummyLLMClient:
    def __init__(self, completions):
        self.chat = _DummyChat(completions)


@pytest.mark.asyncio
async def test_classify_returns_unknown_on_timeout():
    classifier = SmartCitationClassifier()
    classifier.request_timeout = 0.01
    classifier.llm_client = _DummyLLMClient(_DummyCompletionsSleep(sleep_seconds=0.1))

    citing = Paper(id="p1", title="Citing", abstract=None)
    cited = Paper(id="p2", title="Cited", abstract=None)

    result = await classifier.classify(citing, cited)

    assert result.intent == CitationIntent.UNKNOWN
    assert result.confidence == 0.0
    assert result.reasoning == "Timeout"


@pytest.mark.asyncio
async def test_classify_returns_unknown_on_llm_error():
    classifier = SmartCitationClassifier()
    classifier.llm_client = _DummyLLMClient(_DummyCompletionsError())

    citing = Paper(id="p1", title="Citing", abstract=None)
    cited = Paper(id="p2", title="Cited", abstract=None)

    result = await classifier.classify(citing, cited)

    assert result.intent == CitationIntent.UNKNOWN
    assert result.confidence == 0.0
    assert result.reasoning.startswith("Error: ")
