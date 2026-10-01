"""Foundation-only persistence and VIEW eligibility for SPEC-007 M1."""

import os
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import event, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from navox.ai.foundation.contracts import Sensitivity
from navox.connectors.contracts import ConnectorManifest
from navox.db.base import Base
from navox.db.knowledge import KnowledgeChunk, KnowledgeResource, KnowledgeResourcePermission
from navox.db.models import (
    Connection,
    ConnectorConnection,
    ConnectorDefinition,
    ConnectorResource,
    User,
    Workspace,
    WorkspaceMembership,
)
from navox.knowledge.contracts import Permission, PrincipalType, ResourceType
from navox.knowledge.permissions import can_view_resource

NOW = datetime(2026, 9, 29, 12, 0, tzinfo=UTC)
READ_CAPABILITY = "communication.messages.read"
WRITE_CAPABILITY = "communication.messages.send"
UNDECLARED_READ_CAPABILITY = "communication.threads.read"


def manifest() -> ConnectorManifest:
    return ConnectorManifest.model_validate(
        {
            "id": "fixture-mail",
            "version": "1.0.0",
            "displayName": "Fixture mail",
            "category": "test",
            "connectorClass": "GENERIC_API",
            "auth": [{"kind": "none", "label": "None", "scopes": []}],
            "resourceTypes": ["EMAIL"],
            "capabilities": {
                "read": [
                    {
                        "name": READ_CAPABILITY,
                        "description": "Read fixture messages",
                        "sensitive": False,
                    }
                ],
                "write": [
                    {
                        "name": WRITE_CAPABILITY,
                        "description": "Send fixture messages",
                        "sensitive": True,
                    }
                ],
                "events": [],
                "incrementalSync": True,
            },
            "requiredSecrets": [],
            "rateLimitStrategy": "none",
            "minimumNavoxConnectorApiVersion": "1",
        }
    )


@dataclass(frozen=True)
class Seeded:
    workspace_id: UUID
    other_workspace_id: UUID
    user_id: UUID
    other_user_id: UUID
    definition_id: UUID
    connection_id: UUID
    other_connection_id: UUID
    canonical_id: UUID
    resource_id: UUID
    grant_id: UUID


@pytest_asyncio.fixture
async def knowledge_engine(tmp_path: object) -> AsyncIterator[AsyncEngine]:
    dsn = os.environ.get("NAVOX_CONNECTOR_TEST_DSN", f"sqlite+aiosqlite:///{tmp_path}/knowledge.db")
    schema = f"knowledge_{uuid4().hex}"
    admin = None
    if dsn.startswith("postgresql"):
        admin = create_async_engine(dsn)
        async with admin.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        engine = create_async_engine(dsn, connect_args={"server_settings": {"search_path": schema}})
    else:
        engine = create_async_engine(dsn)

        @event.listens_for(engine.sync_engine, "connect")
        def _enable_foreign_keys(dbapi_connection: object, _record: object) -> None:
            cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()
    if admin:
        async with admin.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await admin.dispose()


@pytest_asyncio.fixture
async def knowledge_factory(
    knowledge_engine: AsyncEngine,
) -> async_sessionmaker[AsyncSession]:
    factory = async_sessionmaker(knowledge_engine, expire_on_commit=False)
    await seed(factory)
    return factory


async def seed(factory: async_sessionmaker[AsyncSession]) -> Seeded:
    workspace_id, other_workspace_id = uuid4(), uuid4()
    user_id, other_user_id = uuid4(), uuid4()
    async with factory() as database:
        database.add_all(
            [
                User(id=user_id, email="knowledge@example.com"),
                User(id=other_user_id, email="other-knowledge@example.com"),
                Workspace(id=workspace_id, name="Knowledge"),
                Workspace(id=other_workspace_id, name="Other knowledge"),
            ]
        )
        await database.flush()
        database.add_all(
            [
                WorkspaceMembership(workspace_id=workspace_id, user_id=user_id),
                WorkspaceMembership(workspace_id=other_workspace_id, user_id=other_user_id),
            ]
        )
        definition = ConnectorDefinition(
            connector_key="fixture-mail",
            version="1.0.0",
            display_name="Fixture mail",
            connector_class="GENERIC_API",
            trust_level="NAVOX_FIRST_PARTY",
            manifest=manifest().model_dump(mode="json", by_alias=True),
        )
        database.add(definition)
        await database.flush()
        connection = ConnectorConnection(
            connector_definition_id=definition.id,
            user_id=user_id,
            workspace_id=workspace_id,
            provider="fixture",
            external_account_id="account-1",
            status="CONNECTED",
            health_state="CONNECTED",
            authorized_capabilities=[READ_CAPABILITY, WRITE_CAPABILITY],
            provider_capabilities=[READ_CAPABILITY, WRITE_CAPABILITY],
        )
        other_connection = ConnectorConnection(
            connector_definition_id=definition.id,
            user_id=other_user_id,
            workspace_id=other_workspace_id,
            provider="fixture",
            external_account_id="account-1",
            status="CONNECTED",
            health_state="CONNECTED",
            authorized_capabilities=[READ_CAPABILITY],
            provider_capabilities=[READ_CAPABILITY],
        )
        database.add_all([connection, other_connection])
        await database.flush()
        canonical = ConnectorResource(
            id=uuid4(),
            workspace_id=workspace_id,
            connector_connection_id=connection.id,
            provider="fixture",
            resource_type="EMAIL",
            external_id="message-1",
            canonical={"title": "Quarterly review"},
            retrieved_at=NOW,
            content_hash="a" * 64,
        )
        database.add(canonical)
        await database.flush()
        resource = KnowledgeResource(
            workspace_id=workspace_id,
            owner_user_id=user_id,
            source_type=ResourceType.EMAIL.value,
            source_connection_id=connection.id,
            external_resource_id="message-1",
            source_resource_id=canonical.id,
            source_read_capability=READ_CAPABILITY,
            title="Quarterly review",
            sensitivity=Sensitivity.PERSONAL.value,
            indexed_at=NOW,
        )
        database.add(resource)
        await database.flush()
        grant = KnowledgeResourcePermission(
            resource_id=resource.id,
            workspace_id=workspace_id,
            principal_type=PrincipalType.USER.value,
            principal_id=user_id,
            permission=Permission.VIEW.value,
            valid_from=NOW - timedelta(days=1),
        )
        database.add(grant)
        await database.commit()
        return Seeded(
            workspace_id=workspace_id,
            other_workspace_id=other_workspace_id,
            user_id=user_id,
            other_user_id=other_user_id,
            definition_id=definition.id,
            connection_id=connection.id,
            other_connection_id=other_connection.id,
            canonical_id=canonical.id,
            resource_id=resource.id,
            grant_id=grant.id,
        )


async def seeded(factory: async_sessionmaker[AsyncSession]) -> Seeded:
    async with factory() as database:
        workspace = await database.scalar(select(Workspace).where(Workspace.name == "Knowledge"))
        other_workspace = await database.scalar(
            select(Workspace).where(Workspace.name == "Other knowledge")
        )
        user = await database.scalar(select(User).where(User.email == "knowledge@example.com"))
        other_user = await database.scalar(
            select(User).where(User.email == "other-knowledge@example.com")
        )
        definition = await database.scalar(select(ConnectorDefinition).limit(1))
        resource = await database.scalar(select(KnowledgeResource).limit(1))
        grant = await database.scalar(select(KnowledgeResourcePermission).limit(1))
        assert workspace is not None
        assert other_workspace is not None
        assert user is not None
        assert other_user is not None
        assert definition is not None
        assert resource is not None
        assert grant is not None
        connection = await database.scalar(
            select(ConnectorConnection).where(ConnectorConnection.workspace_id == workspace.id)
        )
        other_connection = await database.scalar(
            select(ConnectorConnection).where(
                ConnectorConnection.workspace_id == other_workspace.id
            )
        )
        canonical = await database.scalar(
            select(ConnectorResource).where(ConnectorResource.workspace_id == workspace.id)
        )
        assert connection is not None
        assert other_connection is not None
        assert canonical is not None
        return Seeded(
            workspace_id=workspace.id,
            other_workspace_id=other_workspace.id,
            user_id=user.id,
            other_user_id=other_user.id,
            definition_id=definition.id,
            connection_id=connection.id,
            other_connection_id=other_connection.id,
            canonical_id=canonical.id,
            resource_id=resource.id,
            grant_id=grant.id,
        )


async def visible(
    factory: async_sessionmaker[AsyncSession],
    case: Seeded,
    *,
    resource_id: UUID | None = None,
    workspace_id: UUID | None = None,
    user_id: UUID | None = None,
    now: datetime = NOW,
) -> bool:
    async with factory() as database:
        return await can_view_resource(
            database,
            resource_id=resource_id or case.resource_id,
            workspace_id=workspace_id or case.workspace_id,
            user_id=user_id or case.user_id,
            now=now,
        )


async def replace_grant(
    factory: async_sessionmaker[AsyncSession], case: Seeded, **values: object
) -> None:
    async with factory() as database:
        row = await database.scalar(
            select(KnowledgeResourcePermission).where(
                KnowledgeResourcePermission.resource_id == case.resource_id
            )
        )
        assert row is not None
        row.principal_type = str(values.get("principal_type", PrincipalType.USER.value))
        row.principal_id = values.get("principal_id", row.principal_id)
        row.permission = str(values.get("permission", Permission.VIEW.value))
        row.valid_from = values.get("valid_from", row.valid_from)
        row.valid_until = values.get("valid_until", row.valid_until)
        row.revoked_at = values.get("revoked_at", row.revoked_at)
        await database.commit()


async def set_resource(
    factory: async_sessionmaker[AsyncSession], case: Seeded, **values: object
) -> None:
    async with factory() as database:
        row = await database.scalar(
            select(KnowledgeResource).where(KnowledgeResource.id == case.resource_id)
        )
        assert row is not None
        for name, value in values.items():
            setattr(row, name, value)
        await database.commit()


async def set_connection(
    factory: async_sessionmaker[AsyncSession], case: Seeded, **values: object
) -> None:
    async with factory() as database:
        row = await database.scalar(
            select(ConnectorConnection).where(ConnectorConnection.id == case.connection_id)
        )
        assert row is not None
        for name, value in values.items():
            setattr(row, name, value)
        await database.commit()


async def set_definition(
    factory: async_sessionmaker[AsyncSession], case: Seeded, **values: object
) -> None:
    async with factory() as database:
        row = await database.scalar(
            select(ConnectorDefinition).where(ConnectorDefinition.id == case.definition_id)
        )
        assert row is not None
        for name, value in values.items():
            setattr(row, name, value)
        await database.commit()


async def set_user(
    factory: async_sessionmaker[AsyncSession], user_id: UUID, **values: object
) -> None:
    async with factory() as database:
        row = await database.scalar(select(User).where(User.id == user_id))
        assert row is not None
        for name, value in values.items():
            setattr(row, name, value)
        await database.commit()


async def add_member(
    factory: async_sessionmaker[AsyncSession],
    case: Seeded,
    *,
    email: str = "member@example.com",
) -> UUID:
    """Add a second active member to the resource's workspace."""
    async with factory() as database:
        member = User(email=email)
        database.add(member)
        await database.flush()
        database.add(WorkspaceMembership(workspace_id=case.workspace_id, user_id=member.id))
        await database.commit()
        return member.id


async def add_nonmember(factory: async_sessionmaker[AsyncSession], email: str) -> UUID:
    async with factory() as database:
        user = User(email=email)
        database.add(user)
        await database.commit()
        return user.id


async def add_foreign_source(
    factory: async_sessionmaker[AsyncSession], case: Seeded
) -> tuple[UUID, UUID]:
    """A connection in the other workspace that is still owned by this user."""
    async with factory() as database:
        connection = ConnectorConnection(
            connector_definition_id=case.definition_id,
            user_id=case.user_id,
            workspace_id=case.other_workspace_id,
            provider="fixture",
            external_account_id="foreign-account",
            status="CONNECTED",
            health_state="CONNECTED",
            authorized_capabilities=[READ_CAPABILITY],
            provider_capabilities=[READ_CAPABILITY],
        )
        database.add(connection)
        await database.flush()
        canonical = ConnectorResource(
            id=uuid4(),
            workspace_id=case.other_workspace_id,
            connector_connection_id=connection.id,
            provider="fixture",
            resource_type="EMAIL",
            external_id="foreign-message",
            canonical={},
            retrieved_at=NOW,
            content_hash="d" * 64,
        )
        database.add(canonical)
        await database.commit()
        return connection.id, canonical.id


async def add_same_workspace_connection(
    factory: async_sessionmaker[AsyncSession], case: Seeded
) -> UUID:
    """A second connection for the same owner, workspace and definition."""
    async with factory() as database:
        connection = ConnectorConnection(
            connector_definition_id=case.definition_id,
            user_id=case.user_id,
            workspace_id=case.workspace_id,
            provider="fixture",
            external_account_id="account-2",
            status="CONNECTED",
            health_state="CONNECTED",
            authorized_capabilities=[READ_CAPABILITY],
            provider_capabilities=[READ_CAPABILITY],
        )
        database.add(connection)
        await database.commit()
        return connection.id


async def visible_with(session: AsyncSession, case: Seeded, *, user_id: UUID | None = None) -> bool:
    """Run the predicate on a caller-owned session so tests can hold ORM rows."""
    return await can_view_resource(
        session,
        resource_id=case.resource_id,
        workspace_id=case.workspace_id,
        user_id=user_id or case.user_id,
        now=NOW,
    )


async def apply_authority_change(
    factory: async_sessionmaker[AsyncSession], case: Seeded, change: str
) -> None:
    """Mutate committed authority in a session the caller does not own."""
    if change == "revoke":
        await replace_grant(factory, case, revoked_at=NOW)
    elif change == "remove_grant":
        async with factory() as database:
            grant = await database.scalar(
                select(KnowledgeResourcePermission).where(
                    KnowledgeResourcePermission.resource_id == case.resource_id
                )
            )
            assert grant is not None
            await database.delete(grant)
            await database.commit()
    elif change == "disconnect":
        await set_connection(factory, case, status="DISCONNECTED", health_state="DISCONNECTED")
    elif change == "disable_definition":
        await set_definition(factory, case, active=False)
    else:
        raise AssertionError(change)


@pytest.mark.asyncio
async def test_explicit_view_grant_is_required(
    knowledge_factory: async_sessionmaker[AsyncSession],
) -> None:
    case = await seeded(knowledge_factory)
    assert await visible(knowledge_factory, case)
    async with knowledge_factory() as database:
        row = await database.scalar(
            select(KnowledgeResourcePermission).where(
                KnowledgeResourcePermission.resource_id == case.resource_id
            )
        )
        assert row is not None
        await database.delete(row)
        await database.commit()
    assert not await visible(knowledge_factory, case)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("principal_type", "principal_is_user", "expected"),
    [
        (PrincipalType.USER, True, True),
        (PrincipalType.WORKSPACE, False, True),
        (PrincipalType.PUBLIC, False, True),
        (PrincipalType.GROUP, False, False),
    ],
)
async def test_principal_kinds_are_scope_bound(
    knowledge_factory: async_sessionmaker[AsyncSession],
    principal_type: PrincipalType,
    principal_is_user: bool,
    expected: bool,
) -> None:
    case = await seeded(knowledge_factory)
    principal = case.user_id if principal_is_user else case.workspace_id
    if principal_type is PrincipalType.PUBLIC:
        principal = None
    elif principal_type is PrincipalType.GROUP:
        principal = uuid4()
    await replace_grant(
        knowledge_factory, case, principal_type=principal_type.value, principal_id=principal
    )
    assert await visible(knowledge_factory, case) is expected


@pytest.mark.asyncio
async def test_workspace_principal_must_name_its_own_workspace(
    knowledge_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A malformed workspace grant can never be persisted, let alone matched."""
    case = await seeded(knowledge_factory)
    async with knowledge_factory() as database:
        database.add(
            KnowledgeResourcePermission(
                resource_id=case.resource_id,
                workspace_id=case.workspace_id,
                principal_type=PrincipalType.WORKSPACE.value,
                principal_id=case.other_workspace_id,
                permission=Permission.VIEW.value,
                valid_from=NOW - timedelta(days=1),
            )
        )
        with pytest.raises(IntegrityError):
            await database.flush()
        await database.rollback()
    # Control: the same workspace grant is accepted when it names its own row.
    async with knowledge_factory() as database:
        database.add(
            KnowledgeResourcePermission(
                resource_id=case.resource_id,
                workspace_id=case.workspace_id,
                principal_type=PrincipalType.WORKSPACE.value,
                principal_id=case.workspace_id,
                permission=Permission.VIEW.value,
                valid_from=NOW - timedelta(days=1),
            )
        )
        await database.commit()
    assert await visible(knowledge_factory, case)


@pytest.mark.asyncio
async def test_shared_user_grant_reaches_a_second_member(
    knowledge_factory: async_sessionmaker[AsyncSession],
) -> None:
    case = await seeded(knowledge_factory)
    member_id = await add_member(knowledge_factory, case)
    assert not await visible(knowledge_factory, case, user_id=member_id)
    await replace_grant(
        knowledge_factory,
        case,
        principal_type=PrincipalType.USER.value,
        principal_id=member_id,
    )
    assert await visible(knowledge_factory, case, user_id=member_id)


@pytest.mark.asyncio
async def test_private_grant_does_not_reach_another_member(
    knowledge_factory: async_sessionmaker[AsyncSession],
) -> None:
    case = await seeded(knowledge_factory)
    member_id = await add_member(knowledge_factory, case)
    assert await visible(knowledge_factory, case)
    assert not await visible(knowledge_factory, case, user_id=member_id)


@pytest.mark.asyncio
async def test_public_grant_still_requires_membership(
    knowledge_factory: async_sessionmaker[AsyncSession],
) -> None:
    case = await seeded(knowledge_factory)
    outsider_id = await add_nonmember(knowledge_factory, "outsider@example.com")
    await replace_grant(
        knowledge_factory, case, principal_type=PrincipalType.PUBLIC.value, principal_id=None
    )
    assert await visible(knowledge_factory, case)
    assert not await visible(knowledge_factory, case, user_id=outsider_id)


@pytest.mark.asyncio
async def test_shared_grant_cannot_outlive_the_source_owner(
    knowledge_factory: async_sessionmaker[AsyncSession],
) -> None:
    case = await seeded(knowledge_factory)
    member_id = await add_member(knowledge_factory, case)
    await replace_grant(
        knowledge_factory,
        case,
        principal_type=PrincipalType.USER.value,
        principal_id=member_id,
    )
    assert await visible(knowledge_factory, case, user_id=member_id)
    await set_user(knowledge_factory, case.user_id, agent_paused=True)
    assert not await visible(knowledge_factory, case, user_id=member_id)
    await set_user(knowledge_factory, case.user_id, agent_paused=False)
    assert await visible(knowledge_factory, case, user_id=member_id)


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["connector_key", "version"])
async def test_manifest_identity_must_match_the_definition(
    knowledge_factory: async_sessionmaker[AsyncSession], field: str
) -> None:
    case = await seeded(knowledge_factory)
    assert await visible(knowledge_factory, case)
    replacement = "other-key" if field == "connector_key" else "9.9.9"
    await set_definition(knowledge_factory, case, **{field: replacement})
    assert not await visible(knowledge_factory, case)


@pytest.mark.asyncio
async def test_declared_read_capability_must_cover_the_bound_capability(
    knowledge_factory: async_sessionmaker[AsyncSession],
) -> None:
    case = await seeded(knowledge_factory)
    await set_connection(
        knowledge_factory,
        case,
        authorized_capabilities=[READ_CAPABILITY, UNDECLARED_READ_CAPABILITY],
        provider_capabilities=[READ_CAPABILITY, UNDECLARED_READ_CAPABILITY],
    )
    assert await visible(knowledge_factory, case)
    await set_resource(knowledge_factory, case, source_read_capability=UNDECLARED_READ_CAPABILITY)
    assert not await visible(knowledge_factory, case)


@pytest.mark.asyncio
@pytest.mark.parametrize("permission", [Permission.COMMENT, Permission.EDIT, Permission.OWNER])
async def test_non_view_permissions_and_ownership_do_not_grant_view(
    knowledge_factory: async_sessionmaker[AsyncSession], permission: Permission
) -> None:
    case = await seeded(knowledge_factory)
    await replace_grant(knowledge_factory, case, permission=permission.value)
    assert not await visible(knowledge_factory, case)


@pytest.mark.asyncio
async def test_wrong_workspace_and_wrong_user_deny(
    knowledge_factory: async_sessionmaker[AsyncSession],
) -> None:
    case = await seeded(knowledge_factory)
    assert not await visible(knowledge_factory, case, workspace_id=case.other_workspace_id)
    assert not await visible(knowledge_factory, case, user_id=case.other_user_id)
    assert not await visible(knowledge_factory, case, resource_id=uuid4())


@pytest.mark.asyncio
async def test_owner_membership_removal_propagates(
    knowledge_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Losing the owner's membership removes the derived resource outright.

    The predicate also re-reads the owner's membership, so both layers deny.
    """
    case = await seeded(knowledge_factory)
    assert await visible(knowledge_factory, case)
    async with knowledge_factory() as database:
        membership = await database.scalar(
            select(WorkspaceMembership).where(
                WorkspaceMembership.workspace_id == case.workspace_id,
                WorkspaceMembership.user_id == case.user_id,
            )
        )
        assert membership is not None
        await database.delete(membership)
        await database.commit()
    async with knowledge_factory() as database:
        assert (
            await database.scalar(
                select(KnowledgeResource).where(KnowledgeResource.id == case.resource_id)
            )
            is None
        )
    assert not await visible(knowledge_factory, case)


@pytest.mark.asyncio
async def test_future_expired_and_revoked_grants_deny(
    knowledge_factory: async_sessionmaker[AsyncSession],
) -> None:
    case = await seeded(knowledge_factory)
    await replace_grant(knowledge_factory, case, valid_from=NOW + timedelta(hours=1))
    assert not await visible(knowledge_factory, case)
    await replace_grant(
        knowledge_factory,
        case,
        valid_from=NOW - timedelta(days=2),
        valid_until=NOW - timedelta(hours=1),
    )
    assert not await visible(knowledge_factory, case)
    await replace_grant(
        knowledge_factory,
        case,
        valid_from=NOW - timedelta(days=2),
        valid_until=None,
        revoked_at=NOW - timedelta(minutes=5),
    )
    assert not await visible(knowledge_factory, case)


@pytest.mark.asyncio
async def test_unbounded_and_expiring_grant_windows(
    knowledge_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A null endpoint is unbounded; the window stays half-open when bounded."""
    case = await seeded(knowledge_factory)
    await replace_grant(
        knowledge_factory, case, valid_from=None, valid_until=NOW + timedelta(hours=1)
    )
    assert await visible(knowledge_factory, case)
    await replace_grant(knowledge_factory, case, valid_from=None, valid_until=None)
    assert await visible(knowledge_factory, case)
    await replace_grant(
        knowledge_factory, case, valid_from=None, valid_until=NOW - timedelta(seconds=1)
    )
    assert not await visible(knowledge_factory, case)
    await replace_grant(
        knowledge_factory, case, valid_from=NOW, valid_until=NOW + timedelta(hours=1)
    )
    assert await visible(knowledge_factory, case)


@pytest.mark.asyncio
async def test_deleted_resource_and_deleted_canonical_source_deny(
    knowledge_factory: async_sessionmaker[AsyncSession],
) -> None:
    case = await seeded(knowledge_factory)
    await set_resource(knowledge_factory, case, deleted_at=NOW)
    assert not await visible(knowledge_factory, case)
    await set_resource(knowledge_factory, case, deleted_at=None)
    assert await visible(knowledge_factory, case)
    async with knowledge_factory() as database:
        canonical = await database.scalar(
            select(ConnectorResource).where(ConnectorResource.workspace_id == case.workspace_id)
        )
        assert canonical is not None
        canonical.deleted = True
        await database.commit()
    assert not await visible(knowledge_factory, case)


@pytest.mark.asyncio
async def test_physical_canonical_deletion_removes_the_derived_resource(
    knowledge_factory: async_sessionmaker[AsyncSession],
) -> None:
    case = await seeded(knowledge_factory)
    async with knowledge_factory() as database:
        canonical = await database.scalar(
            select(ConnectorResource).where(ConnectorResource.workspace_id == case.workspace_id)
        )
        assert canonical is not None
        await database.delete(canonical)
        await database.commit()
    assert not await visible(knowledge_factory, case)
    async with knowledge_factory() as database:
        assert await database.scalar(select(KnowledgeResource).limit(1)) is None
        assert await database.scalar(select(KnowledgeResourcePermission).limit(1)) is None


@pytest.mark.asyncio
async def test_provenance_shell_without_canonical_binding_is_ineligible(
    knowledge_factory: async_sessionmaker[AsyncSession],
) -> None:
    case = await seeded(knowledge_factory)
    async with knowledge_factory() as database:
        shell = KnowledgeResource(
            workspace_id=case.workspace_id,
            owner_user_id=case.user_id,
            source_type=ResourceType.DOCUMENT.value,
            source_connection_id=case.connection_id,
            external_resource_id="document-shell",
            source_resource_id=None,
            source_read_capability=READ_CAPABILITY,
            sensitivity=Sensitivity.PERSONAL.value,
        )
        database.add(shell)
        await database.flush()
        database.add(
            KnowledgeResourcePermission(
                resource_id=shell.id,
                workspace_id=case.workspace_id,
                principal_type=PrincipalType.USER.value,
                principal_id=case.user_id,
                permission=Permission.VIEW.value,
                valid_from=NOW - timedelta(days=1),
            )
        )
        await database.commit()
        shell_id = shell.id
    assert not await visible(knowledge_factory, case, resource_id=shell_id)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "values",
    [
        pytest.param({"status": "DISCONNECTED"}, id="disconnected"),
        pytest.param({"health_state": "PAUSED"}, id="paused"),
        pytest.param({"health_state": "AUTH_EXPIRED"}, id="auth-expired"),
        pytest.param({"health_state": "RATE_LIMITED"}, id="rate-limited"),
    ],
)
async def test_inactive_source_states_deny(
    knowledge_factory: async_sessionmaker[AsyncSession], values: dict[str, object]
) -> None:
    case = await seeded(knowledge_factory)
    await set_connection(knowledge_factory, case, **values)
    assert not await visible(knowledge_factory, case)


@pytest.mark.asyncio
async def test_missing_or_disabled_definition_denies(
    knowledge_factory: async_sessionmaker[AsyncSession],
) -> None:
    case = await seeded(knowledge_factory)
    await set_definition(knowledge_factory, case, active=False)
    assert not await visible(knowledge_factory, case)
    await set_definition(knowledge_factory, case, active=True, manifest={})
    assert not await visible(knowledge_factory, case)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "values",
    [
        pytest.param({"authorized_capabilities": [WRITE_CAPABILITY]}, id="write-only-authorized"),
        pytest.param({"provider_capabilities": [WRITE_CAPABILITY]}, id="write-only-provider"),
        pytest.param({"authorized_capabilities": []}, id="no-authority"),
        pytest.param({"provider_capabilities": []}, id="no-provider-capability"),
    ],
)
async def test_read_capability_withdrawal_denies(
    knowledge_factory: async_sessionmaker[AsyncSession], values: dict[str, object]
) -> None:
    case = await seeded(knowledge_factory)
    await set_connection(knowledge_factory, case, **values)
    assert not await visible(knowledge_factory, case)


@pytest.mark.asyncio
@pytest.mark.parametrize("capability", ["", "messages", "communication.messages.send"])
async def test_unusable_resource_capability_denies(
    knowledge_factory: async_sessionmaker[AsyncSession], capability: str
) -> None:
    case = await seeded(knowledge_factory)
    await set_resource(knowledge_factory, case, source_read_capability=capability)
    assert not await visible(knowledge_factory, case)


@pytest.mark.asyncio
async def test_bound_legacy_connection_must_be_active_and_match_owner(
    knowledge_factory: async_sessionmaker[AsyncSession],
) -> None:
    case = await seeded(knowledge_factory)
    async with knowledge_factory() as database:
        legacy = Connection(
            user_id=case.other_user_id,
            workspace_id=case.workspace_id,
            provider="fixture",
            external_account_id="legacy-1",
            status="active",
        )
        database.add(legacy)
        await database.flush()
        legacy_id = legacy.id
        connection = await database.scalar(
            select(ConnectorConnection).where(ConnectorConnection.id == case.connection_id)
        )
        assert connection is not None
        connection.legacy_connection_id = legacy.id
        await database.commit()
    assert not await visible(knowledge_factory, case)
    async with knowledge_factory() as database:
        legacy = await database.scalar(select(Connection).where(Connection.id == legacy_id))
        assert legacy is not None
        legacy.user_id = case.user_id
        legacy.status = "disconnected"
        await database.commit()
    assert not await visible(knowledge_factory, case)
    async with knowledge_factory() as database:
        legacy = await database.scalar(select(Connection).where(Connection.id == legacy_id))
        assert legacy is not None
        legacy.status = "active"
        await database.commit()
    assert await visible(knowledge_factory, case)


@pytest.mark.asyncio
async def test_owner_mismatch_with_the_source_connection_denies(
    knowledge_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A same-workspace member cannot own a resource on someone else's source."""
    case = await seeded(knowledge_factory)
    member_id = await add_member(knowledge_factory, case)
    async with knowledge_factory() as database:
        database.add(
            KnowledgeResource(
                workspace_id=case.workspace_id,
                owner_user_id=member_id,
                source_type=ResourceType.DOCUMENT.value,
                source_connection_id=case.connection_id,
                external_resource_id="document-1",
                source_read_capability=READ_CAPABILITY,
                sensitivity=Sensitivity.PERSONAL.value,
            )
        )
        with pytest.raises(IntegrityError):
            await database.flush()
        await database.rollback()
    async with knowledge_factory() as database:
        resource = await database.scalar(
            select(KnowledgeResource).where(KnowledgeResource.id == case.resource_id)
        )
        assert resource is not None
        resource.owner_user_id = member_id
        with pytest.raises(IntegrityError):
            await database.flush()
        await database.rollback()


@pytest.mark.asyncio
async def test_metadata_cannot_grant_access(
    knowledge_factory: async_sessionmaker[AsyncSession],
) -> None:
    case = await seeded(knowledge_factory)
    await set_resource(
        knowledge_factory,
        case,
        resource_metadata={
            "permission": "VIEW",
            "principal_type": "PUBLIC",
            "source_read_capability": READ_CAPABILITY,
        },
        source_read_capability="",
    )
    assert not await visible(knowledge_factory, case)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "values",
    [
        pytest.param({"source_type": "SECRET_PLANE"}, id="source-type"),
        pytest.param({"sensitivity": "MOSTLY_PUBLIC"}, id="sensitivity"),
    ],
)
async def test_resource_enum_checks_reject_direct_orm_writes(
    knowledge_factory: async_sessionmaker[AsyncSession], values: dict[str, object]
) -> None:
    """Persisted contract enums cannot be bypassed by a direct ORM write."""
    case = await seeded(knowledge_factory)
    async with knowledge_factory() as database:
        resource = await database.scalar(
            select(KnowledgeResource).where(KnowledgeResource.id == case.resource_id)
        )
        assert resource is not None
        for name, value in values.items():
            setattr(resource, name, value)
        with pytest.raises(IntegrityError):
            await database.flush()
        await database.rollback()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("source_type", "expected"),
    [
        (ResourceType.COMMITMENT, False),
        (ResourceType.SUBSCRIPTION, False),
        (ResourceType.NEWS_STORY, False),
        (ResourceType.TASK, True),
        (ResourceType.OTHER, True),
    ],
)
async def test_structured_domains_fail_closed_until_an_adapter_exists(
    knowledge_factory: async_sessionmaker[AsyncSession],
    source_type: ResourceType,
    expected: bool,
) -> None:
    case = await seeded(knowledge_factory)
    await set_resource(knowledge_factory, case, source_type=source_type.value)
    assert await visible(knowledge_factory, case) is expected


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["revoke", "remove_grant", "disconnect", "disable_definition"])
async def test_loaded_orm_rows_do_not_retain_authority(
    knowledge_engine: AsyncEngine,
    knowledge_factory: async_sessionmaker[AsyncSession],
    change: str,
) -> None:
    """ORM-session regression only: M1 ships no follow-up session, cache or token.

    The reader holds strong references to every object the predicate loads, then
    another session commits a change of authority. The same reader must deny, and
    the retained grant object must reflect the committed revocation.
    """
    case = await seeded(knowledge_factory)
    reader_factory = async_sessionmaker(knowledge_engine, expire_on_commit=False)
    async with reader_factory() as reader:
        loaded = {
            "resource": await reader.scalar(
                select(KnowledgeResource)
                .where(KnowledgeResource.id == case.resource_id)
                .execution_options(populate_existing=True)
            ),
            "grant": await reader.scalar(
                select(KnowledgeResourcePermission)
                .where(KnowledgeResourcePermission.resource_id == case.resource_id)
                .execution_options(populate_existing=True)
            ),
            "connection": await reader.scalar(
                select(ConnectorConnection)
                .where(ConnectorConnection.id == case.connection_id)
                .execution_options(populate_existing=True)
            ),
            "definition": await reader.scalar(
                select(ConnectorDefinition)
                .where(ConnectorDefinition.id == case.definition_id)
                .execution_options(populate_existing=True)
            ),
            "canonical": await reader.scalar(
                select(ConnectorResource)
                .where(ConnectorResource.id == case.canonical_id)
                .execution_options(populate_existing=True)
            ),
            "user": await reader.scalar(
                select(User)
                .where(User.id == case.user_id)
                .execution_options(populate_existing=True)
            ),
            "member": await reader.scalar(
                select(WorkspaceMembership)
                .where(
                    WorkspaceMembership.workspace_id == case.workspace_id,
                    WorkspaceMembership.user_id == case.user_id,
                )
                .execution_options(populate_existing=True)
            ),
        }
        assert all(value is not None for value in loaded.values())
        assert await visible_with(reader, case)
        await reader.commit()
        await apply_authority_change(knowledge_factory, case, change)
        assert all(value is not None for value in loaded.values())
        assert not await visible_with(reader, case)
        if change == "revoke":
            retained = loaded["grant"]
            assert retained is not None
            assert retained.revoked_at is not None


@pytest.mark.asyncio
async def test_naive_now_is_rejected(
    knowledge_factory: async_sessionmaker[AsyncSession],
) -> None:
    case = await seeded(knowledge_factory)
    with pytest.raises(ValueError):
        await visible(knowledge_factory, case, now=datetime(2026, 9, 29, 12, 0))


@pytest.mark.asyncio
async def test_duplicate_source_identity_is_rejected_including_soft_deletes(
    knowledge_factory: async_sessionmaker[AsyncSession],
) -> None:
    case = await seeded(knowledge_factory)
    async with knowledge_factory() as database:
        database.add(
            KnowledgeResource(
                workspace_id=case.workspace_id,
                owner_user_id=case.user_id,
                source_type=ResourceType.FILE.value,
                source_connection_id=case.connection_id,
                external_resource_id="message-1",
                source_read_capability=READ_CAPABILITY,
                sensitivity=Sensitivity.PERSONAL.value,
            )
        )
        with pytest.raises(IntegrityError):
            await database.flush()
        await database.rollback()
    async with knowledge_factory() as database:
        resource = await database.scalar(
            select(KnowledgeResource).where(KnowledgeResource.id == case.resource_id)
        )
        assert resource is not None
        resource.deleted_at = NOW
        await database.commit()
    async with knowledge_factory() as database:
        database.add(
            KnowledgeResource(
                workspace_id=case.workspace_id,
                owner_user_id=case.user_id,
                source_type=ResourceType.EMAIL.value,
                source_connection_id=case.connection_id,
                external_resource_id="message-1",
                source_read_capability=READ_CAPABILITY,
                sensitivity=Sensitivity.PERSONAL.value,
            )
        )
        with pytest.raises(IntegrityError):
            await database.flush()
        await database.rollback()


@pytest.mark.asyncio
async def test_source_identity_is_distinct_per_connection_and_workspace(
    knowledge_factory: async_sessionmaker[AsyncSession],
) -> None:
    case = await seeded(knowledge_factory)
    second_connection_id = await add_same_workspace_connection(knowledge_factory, case)
    async with knowledge_factory() as database:
        for workspace_id, connection_id, owner_id, digest in (
            (case.other_workspace_id, case.other_connection_id, case.other_user_id, "b"),
            (case.workspace_id, second_connection_id, case.user_id, "c"),
        ):
            canonical = ConnectorResource(
                id=uuid4(),
                workspace_id=workspace_id,
                connector_connection_id=connection_id,
                provider="fixture",
                resource_type="EMAIL",
                external_id="message-1",
                canonical={},
                retrieved_at=NOW,
                content_hash=digest * 64,
            )
            database.add(canonical)
            await database.flush()
            database.add(
                KnowledgeResource(
                    workspace_id=workspace_id,
                    owner_user_id=owner_id,
                    source_type=ResourceType.EMAIL.value,
                    source_connection_id=connection_id,
                    external_resource_id="message-1",
                    source_resource_id=canonical.id,
                    source_read_capability=READ_CAPABILITY,
                    sensitivity=Sensitivity.PERSONAL.value,
                )
            )
            await database.flush()
        await database.commit()
    async with knowledge_factory() as database:
        assert len(list(await database.scalars(select(KnowledgeResource)))) == 3


@pytest.mark.asyncio
async def test_resource_owner_must_belong_to_the_resource_workspace(
    knowledge_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Only the owner-membership link is wrong; source and provenance are valid."""
    case = await seeded(knowledge_factory)
    connection_id, canonical_id = await add_foreign_source(knowledge_factory, case)
    async with knowledge_factory() as database:
        database.add(
            KnowledgeResource(
                workspace_id=case.other_workspace_id,
                owner_user_id=case.user_id,
                source_type=ResourceType.EMAIL.value,
                source_connection_id=connection_id,
                external_resource_id="foreign-message",
                source_resource_id=canonical_id,
                source_read_capability=READ_CAPABILITY,
                sensitivity=Sensitivity.PERSONAL.value,
            )
        )
        with pytest.raises(IntegrityError):
            await database.flush()
        await database.rollback()
    # Control: the identical row is accepted once the owner is a member there,
    # so the rejected link above is specifically the owner membership.
    async with knowledge_factory() as database:
        database.add(
            WorkspaceMembership(workspace_id=case.other_workspace_id, user_id=case.user_id)
        )
        await database.commit()
    async with knowledge_factory() as database:
        database.add(
            KnowledgeResource(
                workspace_id=case.other_workspace_id,
                owner_user_id=case.user_id,
                source_type=ResourceType.EMAIL.value,
                source_connection_id=connection_id,
                external_resource_id="foreign-message",
                source_resource_id=canonical_id,
                source_read_capability=READ_CAPABILITY,
                sensitivity=Sensitivity.PERSONAL.value,
            )
        )
        await database.commit()


@pytest.mark.asyncio
async def test_resource_source_connection_must_match_the_resource_workspace(
    knowledge_factory: async_sessionmaker[AsyncSession],
) -> None:
    """The owner matches the connection; only the workspace scope is wrong."""
    case = await seeded(knowledge_factory)
    connection_id, _ = await add_foreign_source(knowledge_factory, case)
    async with knowledge_factory() as database:
        database.add(
            KnowledgeResource(
                workspace_id=case.workspace_id,
                owner_user_id=case.user_id,
                source_type=ResourceType.DOCUMENT.value,
                source_connection_id=connection_id,
                external_resource_id="document-1",
                source_read_capability=READ_CAPABILITY,
                sensitivity=Sensitivity.PERSONAL.value,
            )
        )
        with pytest.raises(IntegrityError):
            await database.flush()
        await database.rollback()
    # Control: the same row is accepted under its own workspace, so the rejected
    # link above is specifically the workspace/connection scope.
    async with knowledge_factory() as database:
        database.add(
            WorkspaceMembership(workspace_id=case.other_workspace_id, user_id=case.user_id)
        )
        await database.flush()
        database.add(
            KnowledgeResource(
                workspace_id=case.other_workspace_id,
                owner_user_id=case.user_id,
                source_type=ResourceType.DOCUMENT.value,
                source_connection_id=connection_id,
                external_resource_id="document-1",
                source_read_capability=READ_CAPABILITY,
                sensitivity=Sensitivity.PERSONAL.value,
            )
        )
        await database.commit()


@pytest.mark.asyncio
async def test_canonical_pointer_must_identify_the_same_external_resource(
    knowledge_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Scope and identity are otherwise valid; only the pointer's target differs."""
    case = await seeded(knowledge_factory)
    async with knowledge_factory() as database:
        canonical = await database.scalar(
            select(ConnectorResource).where(ConnectorResource.id == case.canonical_id)
        )
        assert canonical is not None
        replacement = ConnectorResource(
            id=uuid4(),
            workspace_id=case.workspace_id,
            connector_connection_id=case.connection_id,
            provider="fixture",
            resource_type="FILE",
            external_id="message-9",
            canonical={},
            retrieved_at=NOW,
            content_hash="e" * 64,
        )
        database.add(replacement)
        await database.commit()
        replacement_id = replacement.id
    async with knowledge_factory() as database:
        database.add(
            KnowledgeResource(
                workspace_id=case.workspace_id,
                owner_user_id=case.user_id,
                source_type=ResourceType.EMAIL.value,
                source_connection_id=case.connection_id,
                external_resource_id="message-9",
                source_resource_id=case.canonical_id,
                source_read_capability=READ_CAPABILITY,
                sensitivity=Sensitivity.PERSONAL.value,
            )
        )
        with pytest.raises(IntegrityError):
            await database.flush()
        await database.rollback()
    # Control: pointing at the canonical row for "message-9" is accepted.
    async with knowledge_factory() as database:
        database.add(
            KnowledgeResource(
                workspace_id=case.workspace_id,
                owner_user_id=case.user_id,
                source_type=ResourceType.FILE.value,
                source_connection_id=case.connection_id,
                external_resource_id="message-9",
                source_resource_id=replacement_id,
                source_read_capability=READ_CAPABILITY,
                sensitivity=Sensitivity.PERSONAL.value,
            )
        )
        await database.commit()


@pytest.mark.asyncio
async def test_canonical_pointer_cannot_cross_workspace(
    knowledge_factory: async_sessionmaker[AsyncSession],
) -> None:
    case = await seeded(knowledge_factory)
    _, foreign_canonical_id = await add_foreign_source(knowledge_factory, case)
    async with knowledge_factory() as database:
        database.add(
            KnowledgeResource(
                workspace_id=case.workspace_id,
                owner_user_id=case.user_id,
                source_type=ResourceType.EMAIL.value,
                source_connection_id=case.connection_id,
                external_resource_id="foreign-message",
                source_resource_id=foreign_canonical_id,
                source_read_capability=READ_CAPABILITY,
                sensitivity=Sensitivity.PERSONAL.value,
            )
        )
        with pytest.raises(IntegrityError):
            await database.flush()
        await database.rollback()
    # Control: the same row is accepted against a same-workspace canonical row.
    async with knowledge_factory() as database:
        local = ConnectorResource(
            id=uuid4(),
            workspace_id=case.workspace_id,
            connector_connection_id=case.connection_id,
            provider="fixture",
            resource_type="EMAIL",
            external_id="foreign-message",
            canonical={},
            retrieved_at=NOW,
            content_hash="f" * 64,
        )
        database.add(local)
        await database.flush()
        database.add(
            KnowledgeResource(
                workspace_id=case.workspace_id,
                owner_user_id=case.user_id,
                source_type=ResourceType.EMAIL.value,
                source_connection_id=case.connection_id,
                external_resource_id="foreign-message",
                source_resource_id=local.id,
                source_read_capability=READ_CAPABILITY,
                sensitivity=Sensitivity.PERSONAL.value,
            )
        )
        await database.commit()


@pytest.mark.asyncio
async def test_permission_must_share_the_resource_scope(
    knowledge_factory: async_sessionmaker[AsyncSession],
) -> None:
    case = await seeded(knowledge_factory)
    async with knowledge_factory() as database:
        database.add(
            KnowledgeResourcePermission(
                resource_id=case.resource_id,
                workspace_id=case.other_workspace_id,
                principal_type=PrincipalType.USER.value,
                principal_id=case.other_user_id,
                permission=Permission.VIEW.value,
                valid_from=NOW,
            )
        )
        with pytest.raises(IntegrityError):
            await database.flush()
        await database.rollback()
    # Control: the same grant is accepted inside the resource's own workspace.
    async with knowledge_factory() as database:
        database.add(
            KnowledgeResourcePermission(
                resource_id=case.resource_id,
                workspace_id=case.workspace_id,
                principal_type=PrincipalType.USER.value,
                principal_id=case.other_user_id,
                permission=Permission.VIEW.value,
                valid_from=NOW,
            )
        )
        await database.commit()


@pytest.mark.asyncio
async def test_permission_schema_validates_principals_and_intervals(
    knowledge_factory: async_sessionmaker[AsyncSession],
) -> None:
    case = await seeded(knowledge_factory)
    async with knowledge_factory() as database:
        database.add(
            KnowledgeResourcePermission(
                resource_id=case.resource_id,
                workspace_id=case.workspace_id,
                principal_type=PrincipalType.PUBLIC.value,
                principal_id=case.user_id,
                permission=Permission.VIEW.value,
                valid_from=NOW,
            )
        )
        with pytest.raises(IntegrityError):
            await database.flush()
        await database.rollback()
    async with knowledge_factory() as database:
        database.add(
            KnowledgeResourcePermission(
                resource_id=case.resource_id,
                workspace_id=case.workspace_id,
                principal_type="DELEGATE",
                principal_id=case.user_id,
                permission=Permission.VIEW.value,
                valid_from=NOW,
            )
        )
        with pytest.raises(IntegrityError):
            await database.flush()
        await database.rollback()
    async with knowledge_factory() as database:
        database.add(
            KnowledgeResourcePermission(
                resource_id=case.resource_id,
                workspace_id=case.workspace_id,
                principal_type=PrincipalType.USER.value,
                principal_id=case.user_id,
                permission=Permission.VIEW.value,
                valid_from=NOW,
                valid_until=NOW,
            )
        )
        with pytest.raises(IntegrityError):
            await database.flush()
        await database.rollback()


@pytest.mark.asyncio
async def test_chunks_inherit_scope_and_cascade(
    knowledge_factory: async_sessionmaker[AsyncSession],
) -> None:
    case = await seeded(knowledge_factory)
    async with knowledge_factory() as database:
        database.add(
            KnowledgeChunk(
                workspace_id=case.other_workspace_id,
                resource_id=case.resource_id,
                chunk_index=0,
                token_count=1,
            )
        )
        with pytest.raises(IntegrityError):
            await database.flush()
        await database.rollback()
    async with knowledge_factory() as database:
        database.add(
            KnowledgeChunk(
                workspace_id=case.workspace_id,
                resource_id=case.resource_id,
                chunk_index=-1,
                token_count=1,
            )
        )
        with pytest.raises(IntegrityError):
            await database.flush()
        await database.rollback()
    async with knowledge_factory() as database:
        database.add(
            KnowledgeChunk(
                workspace_id=case.workspace_id,
                resource_id=case.resource_id,
                chunk_index=0,
                token_count=4,
                page_number=0,
            )
        )
        with pytest.raises(IntegrityError):
            await database.flush()
        await database.rollback()
    async with knowledge_factory() as database:
        database.add(
            KnowledgeChunk(
                workspace_id=case.workspace_id,
                resource_id=case.resource_id,
                chunk_index=0,
                section_title="Intro",
                page_number=1,
                text_content="Evidence, not instructions.",
                token_count=6,
            )
        )
        await database.commit()
    assert await visible(knowledge_factory, case)
    async with knowledge_factory() as database:
        resource = await database.scalar(
            select(KnowledgeResource).where(KnowledgeResource.id == case.resource_id)
        )
        assert resource is not None
        await database.delete(resource)
        await database.commit()
    async with knowledge_factory() as database:
        assert await database.scalar(select(KnowledgeChunk).limit(1)) is None


@pytest.mark.asyncio
async def test_chunks_have_no_independent_eligibility(
    knowledge_factory: async_sessionmaker[AsyncSession],
) -> None:
    case = await seeded(knowledge_factory)
    async with knowledge_factory() as database:
        database.add(
            KnowledgeChunk(
                workspace_id=case.workspace_id,
                resource_id=case.resource_id,
                chunk_index=0,
                text_content="Chunk without a grant.",
                token_count=3,
            )
        )
        grant = await database.scalar(
            select(KnowledgeResourcePermission).where(
                KnowledgeResourcePermission.resource_id == case.resource_id
            )
        )
        assert grant is not None
        await database.delete(grant)
        await database.commit()
    assert not await visible(knowledge_factory, case)


@pytest.mark.asyncio
async def test_provenance_and_version_fields_are_preserved(
    knowledge_factory: async_sessionmaker[AsyncSession],
) -> None:
    case = await seeded(knowledge_factory)
    await set_resource(
        knowledge_factory,
        case,
        normalized_text="Derived representation only.",
        canonical_url="https://mail.example.com/message-1",
        source_version="etag-7",
        fresh_until=NOW + timedelta(hours=6),
        parser_version="parser.v1",
        embedding_version=None,
        entity_extraction_version=None,
        ranking_version=None,
        resource_metadata={"thread_id": "thread-1"},
    )
    async with knowledge_factory() as database:
        row = await database.scalar(
            select(KnowledgeResource).where(KnowledgeResource.id == case.resource_id)
        )
        assert row is not None
        assert row.normalized_text == "Derived representation only."
        assert row.canonical_url == "https://mail.example.com/message-1"
        assert row.source_version == "etag-7"
        assert row.resource_metadata == {"thread_id": "thread-1"}
        assert row.embedding_version is None
        assert row.entity_extraction_version is None
        assert row.ranking_version is None
        assert row.parser_version == "parser.v1"
        assert row.deleted_at is None
