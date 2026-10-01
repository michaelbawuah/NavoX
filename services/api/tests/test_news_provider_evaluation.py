"""Provider diagnostics test scoring and spending controls without network access."""

import json
from decimal import Decimal

import pytest
from test_ai_evaluation import CorpusFixtureAdapter, model_for, policy_for

from navox.ai.foundation.adapter import ErrorCode, ProviderResponse
from navox.ai.foundation.contracts import FinishReason, JSONDocument, Provider, Usage
from navox.ai.providers import AdapterFailure, HTTPAdapter
from navox.ai.routing import PolicyRules
from navox.news.ai_contracts import CONVERSATION, EXTRACTION
from navox.news.provider_evaluation import evaluate_news_provider, news_provider_corpus


class NewsAdapter(CorpusFixtureAdapter):
    def __init__(self, behavior="correct"):
        super().__init__(Provider.OPENAI)
        self.behavior = behavior

    def classify_error(self, error):
        return HTTPAdapter.classify_error(self, error)

    async def execute(self, request):
        fixture = news_provider_corpus()[len(self.calls)]
        self.calls.append(request)
        if self.behavior == "rate_limit":
            raise AdapterFailure(ErrorCode.RATE_LIMIT)
        items = {item.id: item for item in fixture.context.items}
        spans = [
            dict(
                item_id=str(i),
                item_revision=1,
                field="headline",
                start=0,
                end=len(items[i].headline),
                claim_type="STATEMENT",
            )
            for i in fixture.expected_item_ids
        ]
        if fixture.binding == EXTRACTION:
            payload = {"claims": spans}
        elif fixture.binding == CONVERSATION:
            payload = {
                "claim_ids": [],
                "excerpts": spans,
                "insufficient_context": fixture.insufficient,
            }
        else:
            heading = "what_is_unclear" if fixture.id == "synthesis-disputed" else "what_happened"
            payload = {
                "headline_item_id": str(fixture.context.items[0].id),
                "sections": [
                    {"heading": heading, "claim_ids": [str(i) for i in fixture.expected_claim_ids]}
                ]
                if fixture.expected_claim_ids
                else [],
            }
        if self.behavior == "empty" and fixture.binding == EXTRACTION:
            payload = {"claims": []}
        if self.behavior == "invent_url":
            payload["url"] = "https://invented.example/claim"
        return ProviderResponse(
            model=request.model,
            output=JSONDocument(text=json.dumps(payload)),
            finish_reason=FinishReason.STOP,
            usage=Usage(input_tokens=10, output_tokens=10),
        )

    def estimate_cost(self, model, usage):
        return Decimal("3") if self.behavior == "overcharge" else Decimal("0.00001")


async def run(adapter, *, model=None, policy=None, budget=Decimal("2")):
    return await evaluate_news_provider(
        adapter=adapter,
        model=model or model_for(Provider.OPENAI).model_copy(update={"enabled": True}),
        policy=policy or policy_for(Provider.OPENAI),
        max_cost=budget,
        mode="offline_fixture",
    )


@pytest.mark.asyncio
async def test_full_fixed_corpus_records_binding_and_never_promotes():
    adapter = NewsAdapter()
    report = await run(adapter)
    assert len(adapter.calls) == len(report.cases) == 10
    assert all(c.passed for c in report.cases)
    assert report.reserved_cost == sum(c.reserved_cost for c in report.cases)
    assert report.reserved_cost <= report.max_cost
    assert not report.qualifies_for_promotion and not report.production_quality_measured
    assert len(report.corpus_digest) == len(report.artifact_digest) == 64
    assert all(c.candidate is not None for c in report.cases)


@pytest.mark.asyncio
async def test_schema_valid_empty_output_does_not_count_as_task_success():
    report = await run(NewsAdapter("empty"))
    assert report.cases[0].provider_succeeded and not report.cases[0].passed
    assert report.cases[0].error_code == "task_expectation_failed"
    assert report.cases[2].passed  # An instruction has no extractable factual claim.


@pytest.mark.asyncio
async def test_fabricated_urls_are_rejected_and_preserved_for_review():
    report = await run(NewsAdapter("invent_url"))
    assert all(not c.passed and c.error_code == "validation_failed" for c in report.cases)
    assert report.cases[0].candidate is not None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "behavior,code", [("rate_limit", "rate_limit"), ("overcharge", "budget_exceeded")]
)
async def test_provider_failure_stops_later_spending(behavior, code):
    adapter = NewsAdapter(behavior)
    report = await run(adapter)
    assert len(adapter.calls) == 1
    assert all(c.error_code == code for c in report.cases)
    assert all(c.reserved_cost == 0 for c in report.cases[1:])


@pytest.mark.asyncio
async def test_budget_reserves_before_any_request_and_unknown_price_cannot_run():
    for model, budget in [
        (model_for(Provider.OPENAI).model_copy(update={"enabled": True}), Decimal("0.000001")),
        (
            model_for(Provider.OPENAI).model_copy(
                update={"enabled": True, "input_cost_per_million": None}
            ),
            Decimal("2"),
        ),
    ]:
        adapter = NewsAdapter()
        report = await run(adapter, model=model, budget=budget)
        assert not adapter.calls and report.reserved_cost == 0
        assert all(c.error_code == "budget_or_context_exceeded" for c in report.cases)


@pytest.mark.asyncio
async def test_no_policy_or_disabled_model_does_not_make_request():
    adapter = NewsAdapter()
    with pytest.raises(ValueError, match="not authorized"):
        await run(adapter, policy=PolicyRules())
    with pytest.raises(ValueError, match="not authorized"):
        await run(adapter, model=model_for(Provider.OPENAI))
    assert not adapter.calls


def test_relation_diagnostics_require_exact_relationships_not_only_valid_json():
    from navox.news.provider_evaluation import meets_expectation, relation_provider_corpus

    for case in relation_provider_corpus():
        item = case.context.items[0]
        relations = [
            {
                "claim_id": str(claim_id),
                "span": {
                    "item_id": str(item_id),
                    "item_revision": item.revision,
                    "field": "headline",
                    "start": 0,
                    "end": len(item.headline),
                },
                "relationship": relationship,
            }
            for claim_id, item_id, relationship in case.expected_relations
        ]
        candidate = JSONDocument(text=json.dumps({"relations": relations}))
        assert meets_expectation(case, candidate)
        if relations:
            relations[0]["relationship"] = (
                "CONTRADICTS" if relations[0]["relationship"] == "SUPPORTS" else "SUPPORTS"
            )
            assert not meets_expectation(
                case, JSONDocument(text=json.dumps({"relations": relations}))
            )
