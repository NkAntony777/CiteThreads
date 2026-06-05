"""Tests for citation intent classifier cache semantics."""

from unittest.mock import AsyncMock

import pytest

from app.models.schemas import CitationIntent, IntentClassificationResult, Paper
from app.services.ai_classifier import SmartCitationClassifier


def _paper(pid: str) -> Paper:
    return Paper(id=pid, title=pid, abstract=None)


@pytest.mark.asyncio
async def test_classify_uses_cache_without_context():
    classifier = SmartCitationClassifier()
    classifier._classify_with_llm = AsyncMock(
        return_value=IntentClassificationResult(
            intent=CitationIntent.SUPPORT,
            confidence=0.9,
            reasoning="cached",
        )
    )

    citing = _paper("p1")
    cited = _paper("p2")

    first = await classifier.classify(citing, cited)
    second = await classifier.classify(citing, cited)

    assert first.intent == CitationIntent.SUPPORT
    assert second.intent == CitationIntent.SUPPORT
    assert classifier._classify_with_llm.await_count == 1


@pytest.mark.asyncio
async def test_classify_uses_cache_with_same_contexts():
    classifier = SmartCitationClassifier()
    classifier._classify_with_llm = AsyncMock(
        return_value=IntentClassificationResult(
            intent=CitationIntent.OPPOSE,
            confidence=0.8,
            reasoning="cached-context",
        )
    )

    citing = _paper("p1")
    cited = _paper("p2")
    contexts = ["A cites B", "We compare", "Prior work"]

    first = await classifier.classify(citing, cited, contexts=contexts)
    second = await classifier.classify(citing, cited, contexts=contexts)

    assert first.intent == CitationIntent.OPPOSE
    assert second.intent == CitationIntent.OPPOSE
    assert classifier._classify_with_llm.await_count == 1


@pytest.mark.asyncio
async def test_classify_does_not_mix_context_and_non_context_cache():
    classifier = SmartCitationClassifier()
    classifier._classify_with_llm = AsyncMock(
        side_effect=[
            IntentClassificationResult(
                intent=CitationIntent.NEUTRAL,
                confidence=0.55,
                reasoning="no-context",
            ),
            IntentClassificationResult(
                intent=CitationIntent.SUPPORT,
                confidence=0.95,
                reasoning="with-context",
            ),
        ]
    )

    citing = _paper("p1")
    cited = _paper("p2")

    no_ctx_first = await classifier.classify(citing, cited)
    ctx_first = await classifier.classify(citing, cited, contexts=["Evidence sentence"])
    no_ctx_second = await classifier.classify(citing, cited)
    ctx_second = await classifier.classify(
        citing, cited, contexts=["Evidence sentence"]
    )

    assert no_ctx_first.intent == CitationIntent.NEUTRAL
    assert no_ctx_second.intent == CitationIntent.NEUTRAL
    assert ctx_first.intent == CitationIntent.SUPPORT
    assert ctx_second.intent == CitationIntent.SUPPORT
    assert classifier._classify_with_llm.await_count == 2


@pytest.mark.asyncio
async def test_classify_context_cache_key_only_uses_first_three_contexts():
    classifier = SmartCitationClassifier()
    classifier._classify_with_llm = AsyncMock(
        return_value=IntentClassificationResult(
            intent=CitationIntent.SUPPORT,
            confidence=0.7,
            reasoning="top3",
        )
    )

    citing = _paper("p1")
    cited = _paper("p2")

    ctx_long = ["a", "b", "c", "d"]
    ctx_short = ["a", "b", "c"]

    first = await classifier.classify(citing, cited, contexts=ctx_long)
    second = await classifier.classify(citing, cited, contexts=ctx_short)

    assert first.intent == CitationIntent.SUPPORT
    assert second.intent == CitationIntent.SUPPORT
    assert classifier._classify_with_llm.await_count == 1
