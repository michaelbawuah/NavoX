"""Bounded, source-asserted identity vocabulary for the connected graph.

Graph rows never store names, addresses, bodies or credentials. One asserted
identity becomes a tenant-scoped digest of a whitelisted, provider-scoped kind,
and the resulting node is anchored to exactly one knowledge resource and
revision. Display names stay derived: they are rebuilt from the current
authorized canonical document on every read, after the same authority,
exclusion and revision checks that gate resource content.

Normalization is deliberately conservative. The exact same assertion is a
RESOLVED source identity; different explicit assertions are DISTINCT *as source
identities* and never a claim about real-world aliases. Unknown kinds, malformed
values and prose mentions stay unavailable, and a display-name-only signal is a
POSSIBLE_MATCH that never expands retrieval as resolved.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from uuid import UUID

from navox.intelligence.contracts import SourceDocument, SourceIdentity

IDENTITY_ENTITY_TYPE = "SOURCE_IDENTITY"
IDENTITY_CLAIM_BOUND = 32
IDENTITY_VALUE_LIMIT = 320
DISPLAY_NAME_LIMIT = 256
RECIPIENT_SCAN_BOUND = 64

# Typed resource nodes. Everything unrecognized stays OTHER so an unmapped
# provider type can never masquerade as a stronger domain claim.
RESOURCE_NODE_TYPES = frozenset(
    {"EMAIL", "EMAIL_THREAD", "EVENT", "DOCUMENT", "COURSE", "COURSE_WORK", "OTHER"}
)
_NODE_TYPE_BY_PROVIDER_TYPE = {"academic.course": "COURSE"}
_NODE_TYPE_BY_SOURCE_TYPE = {
    "EMAIL": "EMAIL",
    "EMAIL_THREAD": "EMAIL_THREAD",
    "CALENDAR_EVENT": "EVENT",
    "DOCUMENT": "DOCUMENT",
    "FILE": "DOCUMENT",
    "CANVAS_ASSIGNMENT": "COURSE_WORK",
    "CANVAS_ANNOUNCEMENT": "COURSE_WORK",
}

# Only these (container node type, container role) pairs may become a persisted
# relation. An address in a signature block, subject line or body sentence is
# not a container role, and name-only mentions never reach this table.
ROLE_RELATIONS: dict[tuple[str, str], str] = {
    ("EMAIL", "AUTHOR"): "SENT",
    ("EMAIL", "RECIPIENT"): "RECEIVED",
    ("EVENT", "AUTHOR"): "ORGANIZED",
    ("EVENT", "RECIPIENT"): "ATTENDED",
}
ROLE_NODE_TYPES = frozenset({"EMAIL", "EVENT"})
ROLE_RELATION_KINDS = frozenset(ROLE_RELATIONS.values())

# Root source-parent relations. PART_OF is the pre-existing containment edge;
# BELONGS_TO types the exact Canvas assignment-to-course membership.
CONTAINMENT_KINDS = frozenset({"PART_OF", "BELONGS_TO"})

# Relations whose evidence is two asserted facts and which are therefore
# derived on read instead of being persisted against a single anchor.
COMPUTED_KINDS = frozenset({"SAME_THREAD", "SAME_SOURCE_IDENTITY"})

_EMAIL_IDENTITY_KINDS = frozenset({"email", "email_address"})
_OPAQUE_IDENTITY_KINDS = frozenset({"provider_subject", "provider_id"})


@dataclass(frozen=True)
class IdentityClaim:
    """One whitelisted, source-asserted identity role. Never persisted as names."""

    kind: str
    provider: str
    digest: str
    role: str
    relation: str
    container: str
    display_name: str | None

    @property
    def identity_key(self) -> str:
        """Exact assertion identity inside one tenant: kind plus scoped value."""
        return f"{self.kind}:{self.digest}"


@dataclass(frozen=True)
class IdentityClaims:
    claims: tuple[IdentityClaim, ...] = ()
    truncated: bool = False
    scanned: int = 0


def node_type(source_type: str, provider_resource_type: str) -> str:
    """Deterministic typed node kind from canonical and provider type names."""
    provider_key = provider_resource_type.strip().casefold()
    mapped = _NODE_TYPE_BY_PROVIDER_TYPE.get(provider_key)
    if mapped is not None:
        return mapped
    return _NODE_TYPE_BY_SOURCE_TYPE.get(source_type.strip().upper(), "OTHER")


def node_supports_roles(value: str) -> bool:
    return value in ROLE_NODE_TYPES


def canonical_identity(
    workspace_id: UUID,
    provider: str | None,
    identity_type: str,
    identity_value: str,
) -> tuple[str, str] | None:
    """Reduce one asserted identity to ``(kind, tenant-scoped digest)``.

    Email follows the existing source normalization exactly: only the whole
    address is casefolded and trimmed, so Gmail dots and ``+`` tags are kept
    verbatim and can never merge two distinct accounts. Opaque provider
    identifiers keep their case and are only ever compared inside one provider
    namespace. The workspace is part of the digest so stored keys cannot be
    correlated across tenants. Unwhitelisted or malformed input returns
    ``None``; nothing is inferred from a missing or unusable value.
    """
    if not isinstance(provider, str) or not isinstance(identity_type, str):
        return None
    if not isinstance(identity_value, str):
        return None
    namespace = provider.strip().casefold()
    if not namespace or len(namespace) > 32:
        return None
    kind = identity_type.strip().casefold()
    raw = identity_value.strip()
    if not raw or len(raw) > IDENTITY_VALUE_LIMIT or any(char.isspace() for char in raw):
        return None
    if kind in _EMAIL_IDENTITY_KINDS:
        canonical_kind, value = "email", raw.casefold()
        if "@" not in value:
            return None
    elif kind in _OPAQUE_IDENTITY_KINDS:
        canonical_kind, value = "provider_subject", raw
    else:
        return None
    digest = sha256(
        f"{workspace_id}\x00{namespace}\x00{canonical_kind}\x00{value}".encode()
    ).hexdigest()
    return canonical_kind, digest


def claims_for(document: SourceDocument, container: str, *, workspace_id: UUID) -> IdentityClaims:
    """Whitelisted container roles of one current source document.

    Only author/recipient container roles of an EMAIL or EVENT node produce
    claims. Recipient scanning is bounded and the retained set is capped and
    sorted by digest, so the same document always yields the same claims and a
    truncated set is reported instead of silently pretending to be complete.
    """
    if not node_supports_roles(container):
        return IdentityClaims()
    asserted: list[tuple[str, SourceIdentity]] = []
    if document.author is not None:
        asserted.append(("AUTHOR", document.author))
    recipients = document.recipients[:RECIPIENT_SCAN_BOUND]
    asserted.extend(("RECIPIENT", identity) for identity in recipients)
    claims: list[IdentityClaim] = []
    seen: set[tuple[str, str, str]] = set()
    for role, identity in asserted:
        relation = ROLE_RELATIONS.get((container, role))
        if relation is None:
            continue
        canonical = canonical_identity(
            workspace_id,
            identity.provider or document.provider,
            identity.identity_type,
            identity.identity_value,
        )
        if canonical is None:
            continue
        kind, digest = canonical
        marker = (kind, digest, role)
        if marker in seen:
            continue
        seen.add(marker)
        claims.append(
            IdentityClaim(
                kind=kind,
                provider=(identity.provider or document.provider).strip().casefold(),
                digest=digest,
                role=role,
                relation=relation,
                container=container,
                display_name=display_name(identity.display_name),
            )
        )
    claims.sort(key=lambda claim: (claim.digest, claim.role))
    truncated = len(claims) > IDENTITY_CLAIM_BOUND or len(document.recipients) > (
        RECIPIENT_SCAN_BOUND
    )
    return IdentityClaims(
        claims=tuple(claims[:IDENTITY_CLAIM_BOUND]),
        truncated=truncated,
        scanned=len(asserted),
    )


def display_name(value: str | None) -> str | None:
    """Bounded display text. Only ever rebuilt from a current authorized source."""
    if not isinstance(value, str):
        return None
    normalized = " ".join(value.split())
    return normalized[:DISPLAY_NAME_LIMIT] if normalized else None


def identity_fingerprint(claims: tuple[IdentityClaim, ...]) -> str:
    """Stable digest of the exact assertion set, including each asserted role."""
    material = "\n".join(sorted(f"{claim.digest}\x00{claim.role}" for claim in claims))
    return sha256(material.encode()).hexdigest()


def identity_revision(base_revision: str, claims: tuple[IdentityClaim, ...]) -> str:
    """Current-revision fence for identity anchors of one source resource.

    Identity metadata is folded into the revision so a changed holder, role or
    address invalidates previous anchors and edges even when the source text
    hash is unchanged.
    """
    return sha256(f"{base_revision}\x00{identity_fingerprint(claims)}".encode()).hexdigest()


def combined_revision(*revisions: str) -> str:
    """Order-independent fence for a relation whose evidence is two asserted facts."""
    return sha256("\x00".join(sorted(revisions)).encode()).hexdigest()


def resource_entity_key(resource_id: UUID) -> str:
    return f"knowledge:{resource_id}"


def identity_entity_key(anchor_resource_id: UUID, claim: IdentityClaim) -> str:
    """Source-scoped anchor key: the same identity under another source differs."""
    return f"identity:{anchor_resource_id}:{claim.kind}:{claim.digest}"


def identity_key_suffix(claim: IdentityClaim) -> str:
    """``LIKE`` suffix shared by every source-scoped anchor of one assertion."""
    return f"%:{claim.kind}:{claim.digest}"
