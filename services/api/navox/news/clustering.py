"""Conservative exact identities and auditable, calibration-gated clustering scores."""

import hashlib
import re
import unicodedata
from enum import StrEnum
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic import Field

from navox.news.contracts import Contract, NewsItemRead


def digest_text(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def normalized_text(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def duplicate_keys(item: NewsItemRead) -> tuple[str, str | None]:
    url = urlsplit(item.canonical_url)
    # Strip only recognized tracking keys. Functional query parameters remain significant.
    query = [
        (key, value)
        for key, value in parse_qsl(url.query, keep_blank_values=True)
        if not key.casefold().startswith("utm_") and key.casefold() not in {"fbclid", "gclid"}
    ]
    identity = urlunsplit((url.scheme, url.netloc.casefold(), url.path, urlencode(query), ""))
    snippet = normalized_text(item.description or "")
    # A shared headline or a tiny generic teaser does not prove a copy.
    copy = (
        digest_text("\n".join((item.language, normalized_text(item.headline), snippet)))
        if len(snippet) >= 80
        else None
    )
    return digest_text(identity), copy


class ClusterDecision(StrEnum):
    JOIN_EXISTING = "JOIN_EXISTING"
    CREATE_NEW = "CREATE_NEW"
    UNCERTAIN = "UNCERTAIN"


class ClusterSignals(Contract):
    semantic_similarity: float = Field(ge=0, le=1)
    entity_overlap: float = Field(ge=0, le=1)
    temporal_proximity: float = Field(ge=0, le=1)
    geographic_overlap: float = Field(ge=0, le=1)
    event_type_similarity: float = Field(ge=0, le=1)
    topic_overlap: float = Field(ge=0, le=1)
    incompatible_events: bool = False

    @property
    def score(self) -> float:
        return (
            0.40 * self.semantic_similarity
            + 0.20 * self.entity_overlap
            + 0.15 * self.temporal_proximity
            + 0.10 * self.geographic_overlap
            + 0.10 * self.event_type_similarity
            + 0.05 * self.topic_overlap
        )


class ClusterCalibration(Contract):
    evaluation_reference: str = Field(min_length=1, max_length=128)
    join_threshold: float = Field(ge=0.7, le=1)
    new_threshold: float = Field(ge=0, le=0.6)


def decide_cluster(
    signals: ClusterSignals, calibration: ClusterCalibration | None
) -> ClusterDecision:
    if signals.incompatible_events:
        return ClusterDecision.CREATE_NEW
    if calibration is None:
        # The score alone is not permission to deploy unmeasured semantic grouping.
        return ClusterDecision.UNCERTAIN
    if signals.score >= calibration.join_threshold and signals.entity_overlap > 0:
        return ClusterDecision.JOIN_EXISTING
    if signals.score <= calibration.new_threshold:
        return ClusterDecision.CREATE_NEW
    return ClusterDecision.UNCERTAIN


def search_terms(value: str) -> set[str]:
    return set(re.findall(r"[\w'-]{3,}", normalized_text(value)))
