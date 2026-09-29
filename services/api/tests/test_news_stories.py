"""Story identity, source independence, live rights and correction propagation."""

from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from test_news_foundation import NOW, OTHER_USER, OTHER_WORKSPACE, USER, WORKSPACE, definition, seed

from navox.db.news import NewsClaim, NewsStory, NewsStoryItem, NewsStoryVersion
from navox.news.clustering import (
    ClusterCalibration,
    ClusterDecision,
    ClusterSignals,
    decide_cluster,
)
from navox.news.contracts import NewsError, NewsItemInput, Verification
from navox.news.evidence import (
    ClaimSpan,
    EvidenceReview,
    admit_claim,
    evaluate_claim,
    refresh_verification,
    review_evidence,
)
from navox.news.ingestion import purge_unavailable, store_item
from navox.news.registry import activate_source, current_rights, revoke_rights
from navox.news.stories import index_item, owned_story, story_view
from navox.news.verification import EvidenceFact, verify


async def item(
    db,
    key="one",
    *,
    headline="An observation was recorded",
    description="The instrument measured a change.",
):
    config = definition(summary_generation_allowed=True).model_copy(
        update={
            "key": f"source-{key}",
            "name": f"Source {key}",
            "domain": f"{key}.example.com",
            "endpoint": f"https://{key}.example.com/feed",
            "article_domains": (f"{key}.example.com",),
            "independence_group": f"origin-{key}",
        }
    )
    source = await activate_source(db, config, workspace_id=WORKSPACE, user_id=USER, now=NOW)
    row = await store_item(
        db,
        source,
        await current_rights(db, source),
        config,
        NewsItemInput(
            external_id=key,
            headline=headline,
            description=description,
            canonical_url=f"https://{key}.example.com/report",
            published_at=NOW,
            categories=("science",),
        ),
        now=NOW,
    )
    return config, source, row


def span(row):
    return ClaimSpan(
        item_id=row.id, item_revision=row.revision, field="headline", start=0, end=len(row.headline)
    )


def review(kind="ORIGINAL_REPORT", relationship="SUPPORTS", origins=frozenset()):
    return EvidenceReview(
        kind=kind,
        relationship=relationship,
        strength="strong",
        origin_groups=origins,
        reference="synthetic-review-fixture",
    )


@pytest.mark.asyncio
async def test_exact_copies_share_story_without_inflating_independence(ai_database):
    await seed(ai_database)
    async with ai_database() as db:
        copy = (
            "A sufficiently long report with identical wording and context copied "
            "from the same original observation."
        )
        a, _, first = await item(db, description=copy)
        b, _, second = await item(db, "two", description=copy)
        definitions = {a.key: a, b.key: b}
        story = await index_item(db, first, definitions, now=NOW)
        duplicate = await index_item(db, second, definitions, now=NOW)
        assert duplicate.id == story.id and story.version == 2
        await index_item(db, second, definitions, now=NOW)
        assert story.version == 2
        claim = await admit_claim(db, story, span(first), definitions, now=NOW)
        for row in (first, second):
            await review_evidence(db, claim, span(row), review(), definitions, now=NOW)
        result = await evaluate_claim(db, claim, definitions, now=NOW)
        assert result.status == Verification.UNCONFIRMED and result.independent_supports == 1
        assert (await story_view(db, story, definitions, now=NOW)).source_count == 2
        assert await db.scalar(select(func.count()).select_from(NewsStory)) == 1


@pytest.mark.asyncio
async def test_same_headline_alone_is_not_an_exact_duplicate(ai_database):
    await seed(ai_database)
    async with ai_database() as db:
        a, _, first = await item(db)
        b, _, second = await item(db, "two")
        definitions = {a.key: a, b.key: b}
        assert (await index_item(db, first, definitions, now=NOW)).id != (
            await index_item(db, second, definitions, now=NOW)
        ).id


@pytest.mark.asyncio
async def test_unreviewed_claims_do_not_promote_and_primary_review_requires_rights(ai_database):
    await seed(ai_database)
    async with ai_database() as db:
        config, source, row = await item(db)
        definitions = {config.key: config}
        story = await index_item(db, row, definitions, now=NOW)
        claim = await admit_claim(db, story, span(row), definitions, now=NOW)
        assert (
            await evaluate_claim(db, claim, definitions, now=NOW)
        ).status == Verification.UNCONFIRMED
        await review_evidence(db, claim, span(row), review("PRIMARY_RECORD"), definitions, now=NOW)
        assert await refresh_verification(db, story, definitions, now=NOW)
        assert (
            await story_view(db, story, definitions, now=NOW)
        ).verification_status == Verification.VERIFIED
        assert not await refresh_verification(db, story, definitions, now=NOW)
        narrowed = config.model_copy(
            update={
                "rights": config.rights.model_copy(update={"summary_generation_allowed": False})
            }
        )
        await activate_source(db, narrowed, workspace_id=WORKSPACE, user_id=USER, now=NOW)
        definitions = {narrowed.key: narrowed}
        with pytest.raises(NewsError, match="rights_denied"):
            await evaluate_claim(db, claim, definitions, now=NOW)
        await purge_unavailable(db, definitions, workspace_id=WORKSPACE, user_id=USER, now=NOW)
        assert await db.scalar(select(func.count()).select_from(NewsClaim)) == 0
        assert (
            await story_view(db, story, definitions, now=NOW)
        ).verification_status == Verification.UNCONFIRMED
        await revoke_rights(db, source, now=NOW)
        assert await db.scalar(select(func.count()).select_from(NewsStory)) == 0


@pytest.mark.asyncio
async def test_source_revision_invalidates_claims_and_records_change(ai_database):
    await seed(ai_database)
    async with ai_database() as db:
        config, source, row = await item(db)
        definitions = {config.key: config}
        story = await index_item(db, row, definitions, now=NOW)
        claim = await admit_claim(db, story, span(row), definitions, now=NOW)
        await review_evidence(db, claim, span(row), review("PRIMARY_RECORD"), definitions, now=NOW)
        await refresh_verification(db, story, definitions, now=NOW)
        new = NewsItemInput(
            external_id=row.external_id,
            headline="Corrected observation",
            canonical_url=row.canonical_url,
            published_at=NOW,
            updated_at=NOW + timedelta(minutes=1),
        )
        await store_item(
            db,
            source,
            await current_rights(db, source),
            config,
            new,
            now=NOW + timedelta(minutes=1),
        )
        assert await db.scalar(select(func.count()).select_from(NewsClaim)) == 0
        await index_item(db, row, definitions, now=NOW + timedelta(minutes=1))
        result = await story_view(db, story, definitions, now=NOW + timedelta(minutes=1))
        assert (
            result.headline == "Corrected observation"
            and result.verification_status == Verification.DEVELOPING
        )
        history = list(
            await db.scalars(select(NewsStoryVersion).order_by(NewsStoryVersion.version))
        )
        assert [entry.change_kind for entry in history] == [
            "DISCOVERED",
            "VERIFICATION_CHANGED",
            "SOURCE_UPDATED",
        ]
        assert history[1].claim_states[str(claim.id)] == "VERIFIED"
        assert "observation" not in str(history[-1].source_snapshot)


@pytest.mark.asyncio
async def test_story_and_evidence_foreign_keys_reject_other_owner(ai_database):
    await seed(ai_database)
    async with ai_database() as db:
        config, _, row = await item(db)
        story = await index_item(db, row, {config.key: config}, now=NOW)
        await db.commit()
        with pytest.raises(NewsError, match="story_unavailable"):
            await owned_story(db, story.id, workspace_id=OTHER_WORKSPACE, user_id=OTHER_USER)
        member = await db.get(NewsStoryItem, row.id)
        member.user_id, member.workspace_id = OTHER_USER, OTHER_WORKSPACE
        with pytest.raises(IntegrityError):
            await db.flush()
        await db.rollback()


def fact(**values):
    return EvidenceFact.model_validate(
        dict(
            item_id=uuid4(),
            source_id=uuid4(),
            source_type="PUBLISHER",
            identity_verified=True,
            relationship="SUPPORTS",
            kind="ORIGINAL_REPORT",
            strong=True,
            reviewed=True,
            independence_group=str(uuid4()),
        )
        | values
    )


def test_verification_circular_origins_attribution_disagreement_and_retraction():
    a = fact(independence_group="a", origin_groups={"b"})
    b = fact(independence_group="b", origin_groups={"c"})
    c = fact(independence_group="c", origin_groups={"a"})
    assert verify([a, b, c]).independent_supports == 1
    assert verify([a, fact()]).status == Verification.CORROBORATED
    assert (
        verify([fact(kind="INTERESTED_PARTY", relationship="ATTRIBUTES")]).status
        == Verification.ATTRIBUTED
    )
    assert (
        verify([fact(source_type="SOCIAL", kind="PRIMARY_RECORD")]).status
        == Verification.UNCONFIRMED
    )
    assert verify([fact(), fact(relationship="CONTRADICTS")]).status == Verification.DISPUTED
    assert verify([fact(relationship="CORRECTS")]).status == Verification.CONTRADICTED
    assert (
        verify([fact(relationship="RETRACTS", origin_withdrawal=True)]).status
        == Verification.RETRACTED
    )
    assert (
        verify([fact(relationship="RETRACTS", origin_withdrawal=False)]).status
        == Verification.UNCONFIRMED
    )
    assert verify([fact(kind="PRIMARY_RECORD", reviewed=False)]).status == Verification.UNCONFIRMED
    assert verify([fact(kind="PRIMARY_RECORD", fresh=False)]).status == Verification.UNCONFIRMED


def test_semantic_clustering_requires_calibration_and_respects_event_conflicts():
    signals = ClusterSignals(
        semantic_similarity=1,
        entity_overlap=1,
        temporal_proximity=1,
        geographic_overlap=1,
        event_type_similarity=1,
        topic_overlap=1,
    )
    assert signals.score == pytest.approx(1)
    assert decide_cluster(signals, None) == ClusterDecision.UNCERTAIN
    calibration = ClusterCalibration(
        evaluation_reference="synthetic-calibration", join_threshold=0.9, new_threshold=0.4
    )
    assert decide_cluster(signals, calibration) == ClusterDecision.JOIN_EXISTING
    assert (
        decide_cluster(signals.model_copy(update={"incompatible_events": True}), calibration)
        == ClusterDecision.CREATE_NEW
    )
