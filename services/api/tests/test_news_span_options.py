import json

import pytest

from navox.ai.foundation.persistence import canonical, digest
from navox.ai.validation import OutputRejected
from navox.news.ai_contracts import (
    EXTRACTION,
    news_artifacts,
    news_context_payload,
    validate_news_output,
)
from navox.news.provider_evaluation import news_provider_corpus


def test_original_published_artifacts_are_immutable():
    expected = {
        (
            "PromptDefinition",
            "news_claim_extraction",
        ): "1430a30ab0963f59bd207e3580c018b21b9aa00fe1d5c4f90ab72d7f337ecdf2",
        (
            "PromptDefinition",
            "news_conversation",
        ): "e0342ad3761917ba1adcfc9f2b5fbc8212ee72587d87d462588484b5e013b10b",
        (
            "PromptDefinition",
            "news_synthesis",
        ): "97e8b56a6f1a8e95b32e8e042550cf7fcea00a7d00324e75e6933e87fcdc7761",
        (
            "SchemaDefinition",
            "news_claim_extraction",
        ): "a335b76784eaaa4071f83824b369557b664848c1625a41a7f2e42ba5ed9d8b42",
        (
            "SchemaDefinition",
            "news_conversation",
        ): "b8b6030b718aff768ba3b469b170ecd087e0fda358460757607b47372e6a17f7",
        (
            "SchemaDefinition",
            "news_synthesis",
        ): "fbcab262983d3783997567cc508a3724abc9bcb11d37442440ebe4605739341d",
    }
    actual = {
        (type(item).__name__, item.reference.name): digest(canonical(item))
        for group in news_artifacts()
        for item in group
        if item.reference.version == "v1" and (type(item).__name__, item.reference.name) in expected
    }
    assert actual == expected


def test_unicode_headline_option_uses_exact_current_revision_and_codepoint_count():
    context = news_provider_corpus()[0].context
    item = context.items[0].model_copy(update={"headline": "Café 🌉 — not open.", "revision": 7})
    context = context.model_copy(update={"items": (item,)})
    payload = json.loads(news_context_payload(context).text)
    span = payload["headline_span_options"][0]
    assert span["end"] == len(item.headline) < len(item.headline.encode())
    assert span["item_revision"] == 7 and span["start"] == 0
    assert span["item_id"] == str(item.id)
    validate_news_output(EXTRACTION, {"claims": [span]}, context)
    span["end"] -= 1
    with pytest.raises(OutputRejected):
        validate_news_output(EXTRACTION, {"claims": [span]}, context)


def test_span_options_do_not_add_or_promote_claims():
    case = next(c for c in news_provider_corpus() if c.id == "extract-instruction")
    payload = json.loads(news_context_payload(case.context).text)
    assert payload["news_context"]["claims"] == []
    assert len(payload["headline_span_options"]) == 1
    assert "verification_status" not in payload["headline_span_options"][0]
