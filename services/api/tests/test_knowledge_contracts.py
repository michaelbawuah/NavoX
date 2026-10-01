"""Contract-level regressions for the SPEC-007 M1 knowledge foundation."""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from navox.ai.foundation.contracts import Sensitivity
from navox.knowledge.contracts import (
    STRUCTURED_RESOURCE_TYPES,
    KnowledgeChunkInput,
    KnowledgeResourceInput,
    Permission,
    PermissionGrant,
    PrincipalType,
    ResourceType,
    permission_interval_active,
    principal_matches,
)

NOW = datetime(2026, 9, 29, 12, 0, tzinfo=UTC)
WORKSPACE = uuid4()
OTHER_WORKSPACE = uuid4()
USER = uuid4()
OTHER_USER = uuid4()
CONNECTION = uuid4()
RESOURCE = uuid4()


def resource(**overrides: object) -> KnowledgeResourceInput:
    values: dict[str, object] = {
        "workspace_id": WORKSPACE,
        "owner_user_id": USER,
        "source_type": ResourceType.EMAIL,
        "source_connection_id": CONNECTION,
        "external_resource_id": "message-1",
        "source_read_capability": "communication.messages.read",
        "title": "Quarterly review",
        "sensitivity": Sensitivity.PERSONAL,
    }
    values.update(overrides)
    return KnowledgeResourceInput(**values)


def grant(**overrides: object) -> PermissionGrant:
    values: dict[str, object] = {
        "resource_id": RESOURCE,
        "workspace_id": WORKSPACE,
        "principal_type": PrincipalType.USER,
        "principal_id": USER,
        "permission": Permission.VIEW,
        "valid_from": NOW - timedelta(days=1),
    }
    values.update(overrides)
    return PermissionGrant(**values)


def chunk(**overrides: object) -> KnowledgeChunkInput:
    values: dict[str, object] = {
        "workspace_id": WORKSPACE,
        "resource_id": RESOURCE,
        "chunk_index": 0,
        "token_count": 12,
    }
    values.update(overrides)
    return KnowledgeChunkInput(**values)


def test_resource_type_enum_covers_the_authoritative_extract() -> None:
    assert {item.value for item in ResourceType} == {
        "EMAIL",
        "EMAIL_THREAD",
        "CALENDAR_EVENT",
        "DOCUMENT",
        "FILE",
        "CANVAS_ASSIGNMENT",
        "CANVAS_ANNOUNCEMENT",
        "MEETING",
        "COMMITMENT",
        "SUBSCRIPTION",
        "NEWS_STORY",
        "TASK",
        "OTHER",
    }


def test_structured_domains_are_ineligible_until_an_adapter_exists() -> None:
    assert STRUCTURED_RESOURCE_TYPES == frozenset(
        {ResourceType.COMMITMENT, ResourceType.SUBSCRIPTION, ResourceType.NEWS_STORY}
    )


def test_sensitivity_reuses_spec_005_values() -> None:
    assert set(Sensitivity) == {
        Sensitivity.PUBLIC,
        Sensitivity.INTERNAL,
        Sensitivity.PERSONAL,
        Sensitivity.SENSITIVE,
        Sensitivity.RESTRICTED,
    }
    assert resource(sensitivity=Sensitivity.RESTRICTED).sensitivity is Sensitivity.RESTRICTED
    with pytest.raises(ValidationError):
        resource(sensitivity="SECRET")


def test_resource_contract_is_frozen_and_forbids_unknown_fields() -> None:
    captured = resource()
    with pytest.raises(ValidationError):
        captured.title = "Changed"
    with pytest.raises(ValidationError):
        resource(permission="VIEW")


def test_resource_identity_is_workspace_connection_and_external_id() -> None:
    captured = resource()
    assert captured.identity == (WORKSPACE, CONNECTION, "message-1")
    assert captured.source_type is ResourceType.EMAIL


@pytest.mark.parametrize(
    "capability",
    ["", "   ", "messages", "Messages.Read", "communication..read", "communication.messages.read "],
)
def test_capability_must_be_a_dotted_source_read_capability(capability: str) -> None:
    with pytest.raises((ValidationError, ValueError)):
        resource(source_read_capability=capability)


@pytest.mark.parametrize("field", ["source_created_at", "source_updated_at", "indexed_at"])
def test_naive_resource_timestamps_are_rejected(field: str) -> None:
    with pytest.raises((ValidationError, ValueError)):
        resource(**{field: datetime(2026, 9, 29, 12, 0)})


def test_naive_grant_timestamps_are_rejected() -> None:
    with pytest.raises((ValidationError, ValueError)):
        grant(valid_from=datetime(2026, 9, 29, 12, 0))
    with pytest.raises((ValidationError, ValueError)):
        grant(valid_until=datetime(2026, 9, 30, 12, 0))
    with pytest.raises((ValidationError, ValueError)):
        grant(revoked_at=datetime(2026, 9, 30, 12, 0))


@pytest.mark.parametrize("principal", [PrincipalType.USER, PrincipalType.WORKSPACE])
def test_non_public_grants_require_a_principal(principal: PrincipalType) -> None:
    with pytest.raises(ValidationError):
        grant(principal_type=principal, principal_id=None)
    with pytest.raises(ValidationError):
        grant(principal_type=principal.value, principal_id=None)


def test_public_and_group_principal_rules() -> None:
    assert grant(principal_type=PrincipalType.PUBLIC, principal_id=None).principal_id is None
    with pytest.raises(ValidationError):
        grant(principal_type=PrincipalType.PUBLIC, principal_id=USER)
    assert grant(principal_type=PrincipalType.GROUP, principal_id=uuid4()).principal_id is not None


def test_permission_interval_is_half_open() -> None:
    window = grant(valid_from=NOW, valid_until=NOW + timedelta(hours=1))
    assert window.permits_view(workspace_id=WORKSPACE, user_id=USER, now=NOW)
    assert window.permits_view(
        workspace_id=WORKSPACE, user_id=USER, now=NOW + timedelta(minutes=59)
    )
    assert not window.permits_view(
        workspace_id=WORKSPACE, user_id=USER, now=NOW + timedelta(hours=1)
    )
    assert not window.permits_view(
        workspace_id=WORKSPACE, user_id=USER, now=NOW - timedelta(seconds=1)
    )


@pytest.mark.parametrize(
    "valid_until",
    [pytest.param(NOW, id="empty"), pytest.param(NOW - timedelta(hours=1), id="inverted")],
)
def test_empty_or_inverted_grant_intervals_are_rejected(valid_until: datetime) -> None:
    with pytest.raises(ValidationError):
        grant(valid_from=NOW, valid_until=valid_until)


def test_unbounded_grant_bounds_are_allowed() -> None:
    """A null endpoint is unbounded, per the approved M1 contract."""
    unbounded_start = grant(valid_from=None, valid_until=NOW + timedelta(hours=1))
    assert unbounded_start.valid_from is None
    assert unbounded_start.permits_view(workspace_id=WORKSPACE, user_id=USER, now=NOW)
    both_unbounded = grant(valid_from=None, valid_until=None)
    assert both_unbounded.permits_view(
        workspace_id=WORKSPACE, user_id=USER, now=NOW - timedelta(days=365)
    )
    assert both_unbounded.permits_view(
        workspace_id=WORKSPACE, user_id=USER, now=NOW + timedelta(days=365)
    )
    assert not grant(valid_from=None, valid_until=NOW).permits_view(
        workspace_id=WORKSPACE, user_id=USER, now=NOW
    )
    assert not grant(valid_from=None, valid_until=NOW - timedelta(seconds=1)).permits_view(
        workspace_id=WORKSPACE, user_id=USER, now=NOW
    )


@pytest.mark.parametrize(
    ("principal_type", "principal_id"),
    [
        pytest.param(PrincipalType.USER, USER, id="user"),
        pytest.param(PrincipalType.PUBLIC, None, id="public"),
        pytest.param(PrincipalType.WORKSPACE, WORKSPACE, id="workspace"),
    ],
)
def test_grants_are_bound_to_their_own_workspace(
    principal_type: PrincipalType, principal_id: UUID | None
) -> None:
    """A grant stored for one workspace can never match another workspace."""
    stored = grant(principal_type=principal_type, principal_id=principal_id)
    assert stored.permits_view(workspace_id=WORKSPACE, user_id=USER, now=NOW)
    assert not stored.permits_view(workspace_id=OTHER_WORKSPACE, user_id=USER, now=NOW)
    assert not stored.permits_view(workspace_id=OTHER_WORKSPACE, user_id=OTHER_USER, now=NOW)


def test_workspace_grant_must_name_its_own_workspace() -> None:
    with pytest.raises(ValidationError):
        grant(principal_type=PrincipalType.WORKSPACE, principal_id=OTHER_WORKSPACE)
    with pytest.raises(ValidationError):
        grant(principal_type="WORKSPACE", principal_id=OTHER_WORKSPACE)


def test_revoked_grant_never_permits_view() -> None:
    assert not grant(revoked_at=NOW - timedelta(minutes=1)).permits_view(
        workspace_id=WORKSPACE, user_id=USER, now=NOW
    )
    assert not grant(revoked_at=NOW + timedelta(days=1)).permits_view(
        workspace_id=WORKSPACE, user_id=USER, now=NOW
    )


@pytest.mark.parametrize("permission", [Permission.COMMENT, Permission.EDIT, Permission.OWNER])
def test_only_an_explicit_view_grant_permits_view(permission: Permission) -> None:
    assert not grant(permission=permission).permits_view(
        workspace_id=WORKSPACE, user_id=USER, now=NOW
    )


def test_principal_matching_is_scope_bound_and_groups_fail_closed() -> None:
    assert principal_matches(PrincipalType.USER, USER, workspace_id=WORKSPACE, user_id=USER)
    assert not principal_matches(
        PrincipalType.USER, OTHER_USER, workspace_id=WORKSPACE, user_id=USER
    )
    assert principal_matches(
        PrincipalType.WORKSPACE, WORKSPACE, workspace_id=WORKSPACE, user_id=USER
    )
    assert not principal_matches(
        PrincipalType.WORKSPACE, OTHER_WORKSPACE, workspace_id=WORKSPACE, user_id=USER
    )
    assert principal_matches(PrincipalType.PUBLIC, None, workspace_id=WORKSPACE, user_id=USER)
    assert not principal_matches(PrincipalType.PUBLIC, USER, workspace_id=WORKSPACE, user_id=USER)
    assert not principal_matches(PrincipalType.GROUP, uuid4(), workspace_id=WORKSPACE, user_id=USER)
    assert not principal_matches("UNKNOWN", USER, workspace_id=WORKSPACE, user_id=USER)


def test_permission_interval_helper_normalizes_stored_values() -> None:
    naive = datetime(2026, 9, 29, 12, 0)
    assert permission_interval_active(valid_from=naive, valid_until=None, revoked_at=None, now=NOW)
    assert not permission_interval_active(
        valid_from=naive, valid_until=None, revoked_at=naive, now=NOW
    )
    assert not permission_interval_active(
        valid_from=datetime(2026, 9, 30, 12, 0), valid_until=None, revoked_at=None, now=NOW
    )


@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param({"chunk_index": -1}, id="negative-index"),
        pytest.param({"token_count": -1}, id="negative-tokens"),
        pytest.param({"page_number": 0}, id="zero-page"),
    ],
)
def test_chunk_bounds_are_enforced(overrides: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        chunk(**overrides)


def test_chunk_text_is_optional_for_structured_sources() -> None:
    captured = chunk(page_number=1, section_title="Agenda")
    assert captured.text_content is None
    assert captured.embedding_version is None


def test_metadata_is_untrusted_data_and_cannot_supply_authority() -> None:
    captured = resource(
        metadata={
            "permission": "VIEW",
            "source_read_capability": "communication.messages.read",
            "principal_type": "PUBLIC",
        }
    )
    assert set(captured.metadata) == {"permission", "source_read_capability", "principal_type"}
    assert captured.source_read_capability == "communication.messages.read"
    with pytest.raises((ValidationError, ValueError)):
        resource(source_read_capability="", metadata={"source_read_capability": "ok.read"})
