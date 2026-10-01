"""Application-owned verification. Popularity and model confidence are not evidence."""

from enum import StrEnum
from uuid import UUID

from pydantic import Field

from navox.news.contracts import Contract, SourceType, Verification


class Relationship(StrEnum):
    SUPPORTS = "SUPPORTS"
    CONTRADICTS = "CONTRADICTS"
    ATTRIBUTES = "ATTRIBUTES"
    CORRECTS = "CORRECTS"
    RETRACTS = "RETRACTS"


class EvidenceKind(StrEnum):
    UNREVIEWED = "UNREVIEWED"
    ORIGINAL_REPORT = "ORIGINAL_REPORT"
    PRIMARY_RECORD = "PRIMARY_RECORD"
    INTERESTED_PARTY = "INTERESTED_PARTY"
    SYNDICATED = "SYNDICATED"
    SOCIAL = "SOCIAL"


class EvidenceFact(Contract):
    item_id: UUID
    source_id: UUID
    source_type: SourceType
    identity_verified: bool
    relationship: Relationship
    kind: EvidenceKind
    strong: bool
    reviewed: bool
    # These are reviewed source identities/origins, not model-provided labels.
    independence_group: str = Field(min_length=1)
    origin_groups: frozenset[str] = frozenset()
    copy_digest: str | None = None
    fresh: bool = True
    # A withdrawal must be bound to the claim's originating source.
    origin_withdrawal: bool = False


class VerificationResult(Contract):
    status: Verification
    independent_supports: int
    independent_contradictions: int
    reason: str


def independent_count(facts: list[EvidenceFact]) -> int:
    """Connected components collapse wire copies and transitive/circular citations."""
    groups: list[set[str]] = []
    for fact in facts:
        keys = {f"source:{fact.source_id}", f"origin:{fact.independence_group}"}
        keys.update(f"origin:{group}" for group in fact.origin_groups)
        if fact.copy_digest:
            keys.add(f"copy:{fact.copy_digest}")
        merged = [group for group in groups if group & keys]
        for group in merged:
            keys |= group
            groups.remove(group)
        groups.append(keys)
    return len(groups)


def verify(facts: list[EvidenceFact], *, developing: bool = False) -> VerificationResult:
    usable = [fact for fact in facts if fact.fresh and fact.identity_verified and fact.reviewed]
    strong = [
        fact
        for fact in usable
        if fact.strong
        and fact.source_type != SourceType.SOCIAL
        and fact.kind in {EvidenceKind.ORIGINAL_REPORT, EvidenceKind.PRIMARY_RECORD}
    ]
    support = [fact for fact in strong if fact.relationship == Relationship.SUPPORTS]
    contrary = [
        fact
        for fact in strong
        if fact.relationship in {Relationship.CONTRADICTS, Relationship.CORRECTS}
    ]
    supports, contradictions = independent_count(support), independent_count(contrary)
    if any(
        fact.origin_withdrawal and fact.relationship == Relationship.RETRACTS for fact in usable
    ):
        state, reason = Verification.RETRACTED, "origin_withdrawal"
    elif supports and contradictions:
        state, reason = Verification.DISPUTED, "credible_disagreement"
    elif contradictions:
        state, reason = Verification.CONTRADICTED, "strong_contrary_evidence"
    elif any(fact.kind == EvidenceKind.PRIMARY_RECORD for fact in support):
        state, reason = Verification.VERIFIED, "direct_primary_record"
    elif supports >= 2:
        state, reason = Verification.CORROBORATED, "independent_reports"
    elif any(
        fact.relationship == Relationship.ATTRIBUTES or fact.kind == EvidenceKind.INTERESTED_PARTY
        for fact in usable
    ):
        state, reason = Verification.ATTRIBUTED, "attributed_statement"
    else:
        state = Verification.DEVELOPING if developing else Verification.UNCONFIRMED
        reason = "insufficient_evidence"
    return VerificationResult(
        status=state,
        independent_supports=supports,
        independent_contradictions=contradictions,
        reason=reason,
    )
