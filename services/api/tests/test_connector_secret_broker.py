"""Tenant binding, expiry, key selection and redaction for the privileged broker."""

import asyncio
import copy
import json
import pickle
from collections.abc import AsyncIterator
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from cryptography.fernet import Fernet
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from navox.connectors.secrets import ScopedSecretLease, SecretBroker, SecretBrokerError
from navox.core.settings import Settings
from navox.db.base import Base
from navox.db.models import (
    AuditEvent,
    Connection,
    ConnectionCredential,
    ConnectorConnection,
    ConnectorDefinition,
    User,
    Workspace,
    WorkspaceMembership,
)

SECRET = "synthetic-token-never-log-this"


@pytest_asyncio.fixture
async def database(tmp_path) -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'broker.db'}")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with factory() as session:
        session.info["factory"] = factory
        yield session
    await engine.dispose()


@pytest.fixture
def broker() -> SecretBroker:
    return SecretBroker(
        Settings(_env_file=None, connector_secret_encryption_key=Fernet.generate_key().decode())
    )


async def owner(database: AsyncSession, broker: SecretBroker) -> ConnectorConnection:
    key = str(uuid4())
    user = User(email=f"{key}@example.com")
    workspace = Workspace(name="Personal")
    database.add_all([user, workspace])
    await database.flush()
    database.add(WorkspaceMembership(workspace_id=workspace.id, user_id=user.id))
    definition = ConnectorDefinition(
        connector_key=f"demo-{key}",
        version="1.0.0",
        display_name="Demo",
        connector_class="TOKEN_API",
        trust_level="USER_PRIVATE",
        manifest={},
    )
    database.add(definition)
    await database.flush()
    connection = ConnectorConnection(
        connector_definition_id=definition.id,
        user_id=user.id,
        workspace_id=workspace.id,
        provider="demo",
        external_account_id=key,
        authorized_capabilities=["demo.items.read"],
        provider_capabilities=["demo.items.read"],
        config={},
    )
    database.add(connection)
    await database.flush()
    await broker.store(
        database, {"API_TOKEN": SECRET, "OTHER_SECRET": "other"}, **scope(connection)
    )
    await database.commit()
    return connection


def scope(connection: ConnectorConnection) -> dict[str, UUID]:
    return {
        "connection_id": connection.id,
        "workspace_id": connection.workspace_id,
        "user_id": connection.user_id,
    }


async def lease(database, broker, connection):
    return await broker.lease(
        database, **scope(connection), purpose="sync.read", names={"API_TOKEN"}
    )


@pytest.mark.asyncio
async def test_secret_broker_scopes_and_redacts_secret_values(database, broker):
    connection = await owner(database, broker)
    credential = await database.get(ConnectionCredential, connection.credential_reference)
    assert credential is not None and SECRET not in credential.encrypted_refresh_token
    with await lease(database, broker, connection) as handle:
        assert handle.get("API_TOKEN") == SECRET
        assert "OTHER_SECRET" not in handle.names
        assert SECRET not in repr(handle)
        with pytest.raises(SecretBrokerError, match="not available"):
            handle.get("OTHER_SECRET")
    with pytest.raises(SecretBrokerError, match="closed"):
        handle.get("API_TOKEN")
    await database.flush()
    audits = list(await database.scalars(select(AuditEvent)))
    assert {item.event_type for item in audits} == {
        "connector.credentials.stored",
        "connector.credentials.leased",
    }
    assert SECRET not in json.dumps([item.event_metadata for item in audits])


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["store", "lease", "delete"])
@pytest.mark.parametrize("wrong", ["user_id", "workspace_id", "connection_id"])
async def test_broker_operations_require_matching_owner_and_workspace(
    database, broker, operation, wrong
):
    connection = await owner(database, broker)
    original = connection.credential_reference
    values = scope(connection)
    values[wrong] = uuid4()
    with pytest.raises(SecretBrokerError, match="access denied"):
        if operation == "store":
            await broker.store(database, {"API_TOKEN": "replacement"}, **values)
        elif operation == "lease":
            await broker.lease(database, **values, purpose="sync.read", names={"API_TOKEN"})
        else:
            await broker.delete_for_connection(database, **values)
    assert connection.credential_reference == original
    assert await database.get(ConnectionCredential, original) is not None


@pytest.mark.asyncio
async def test_secret_broker_cannot_lease_another_connections_bundle(database, broker):
    first = await owner(database, broker)
    second = await owner(database, broker)
    second.credential_reference = first.credential_reference
    await database.commit()
    with pytest.raises(SecretBrokerError, match="binding"):
        await lease(database, broker, second)
    with pytest.raises(SecretBrokerError, match="binding"):
        await broker.delete_for_connection(database, **scope(second))
    with await lease(database, broker, first) as valid:
        assert valid.get("API_TOKEN") == SECRET


@pytest.mark.asyncio
async def test_swapping_ciphertext_into_different_credential_row_is_rejected(database, broker):
    connection = await owner(database, broker)
    credential = await database.get(ConnectionCredential, connection.credential_reference)
    copy_row = ConnectionCredential(
        encrypted_refresh_token=credential.encrypted_refresh_token,
        key_version=credential.key_version,
    )
    database.add(copy_row)
    await database.flush()
    connection.credential_reference = copy_row.id
    await database.commit()
    with pytest.raises(SecretBrokerError, match="binding"):
        await lease(database, broker, connection)


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["PAUSED", "DISCONNECTED", "AUTH_EXPIRED", "invalid"])
async def test_new_secret_leases_reject_inactive_connections(database, broker, state):
    connection = await owner(database, broker)
    connection.status = state
    await database.commit()
    with pytest.raises(SecretBrokerError, match="access denied"):
        await lease(database, broker, connection)


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["owner_paused", "membership_removed", "definition_disabled"])
async def test_revoked_owner_or_connector_authority_blocks_new_leases(database, broker, reason):
    connection = await owner(database, broker)
    if reason == "owner_paused":
        user = await database.get(User, connection.user_id)
        user.agent_paused = True
    elif reason == "membership_removed":
        membership = await database.get(
            WorkspaceMembership, (connection.workspace_id, connection.user_id)
        )
        await database.delete(membership)
    else:
        definition = await database.get(ConnectorDefinition, connection.connector_definition_id)
        definition.active = False
    await database.commit()
    with pytest.raises(SecretBrokerError, match="access denied"):
        await lease(database, broker, connection)


@pytest.mark.asyncio
async def test_broker_refreshes_stale_session_after_another_session_pauses_connection(
    database, broker
):
    connection = await owner(database, broker)
    async with database.info["factory"]() as other:
        changed = await other.get(ConnectorConnection, connection.id)
        changed.status = "PAUSED"
        await other.commit()
    assert connection.status == "CONNECTED"
    with pytest.raises(SecretBrokerError, match="access denied"):
        await lease(database, broker, connection)


@pytest.mark.asyncio
@pytest.mark.parametrize("purpose", ["execute", "sync.write", "health", "reveal.secrets", ""])
async def test_secret_purposes_are_allowlisted_not_just_well_formed(database, broker, purpose):
    connection = await owner(database, broker)
    with pytest.raises(SecretBrokerError, match="purpose"):
        await broker.lease(database, **scope(connection), purpose=purpose, names={"API_TOKEN"})


@pytest.mark.asyncio
@pytest.mark.parametrize("names", [set(), {"UNSTORED_TOKEN"}, {"bad name"}])
async def test_secret_scope_must_be_nonempty_valid_and_in_the_bound_bundle(database, broker, names):
    connection = await owner(database, broker)
    with pytest.raises(SecretBrokerError):
        await broker.lease(database, **scope(connection), purpose="sync.read", names=names)


def test_lease_expires_using_monotonic_time_and_close_is_idempotent():
    now = [10.0]
    handle = ScopedSecretLease(
        uuid4(), "sync.read", {"API_TOKEN": SECRET}, ttl_seconds=5, clock=lambda: now[0]
    )
    assert handle.get("API_TOKEN") == SECRET
    now[0] = 15.0
    with pytest.raises(SecretBrokerError, match="expired"):
        handle.get("API_TOKEN")
    handle.close()
    handle.close()
    assert SECRET not in repr(handle)


@pytest.mark.parametrize("ttl", [0, -1, 301, float("inf"), float("nan")])
def test_lease_cannot_disable_its_expiry(ttl):
    with pytest.raises(SecretBrokerError, match="lifetime"):
        ScopedSecretLease(uuid4(), "sync.read", {"API_TOKEN": SECRET}, ttl_seconds=ttl)


@pytest.mark.parametrize("serialize", [pickle.dumps, copy.copy, copy.deepcopy, json.dumps])
def test_lease_does_not_serialize_or_copy_credentials(serialize):
    handle = ScopedSecretLease(uuid4(), "sync.read", {"API_TOKEN": SECRET})
    with pytest.raises(TypeError):
        serialize(handle)
    handle.close()


@pytest.mark.parametrize("error_type", [RuntimeError, asyncio.CancelledError])
def test_lease_context_closes_even_on_failure_or_cancellation(error_type):
    handle = ScopedSecretLease(uuid4(), "health.read", {"API_TOKEN": SECRET})
    with pytest.raises(error_type):
        with handle:
            raise error_type()
    with pytest.raises(SecretBrokerError, match="closed"):
        handle.get("API_TOKEN")


@pytest.mark.asyncio
async def test_local_fallback_bundle_survives_adding_a_separate_primary_key(database):
    fallback = Fernet.generate_key().decode()
    local = SecretBroker(Settings(_env_file=None, google_token_encryption_key=fallback))
    connection = await owner(database, local)
    separate = SecretBroker(
        Settings(
            _env_file=None,
            google_token_encryption_key=fallback,
            connector_secret_encryption_key=Fernet.generate_key().decode(),
        )
    )
    with await lease(database, separate, connection) as handle:
        assert handle.get("API_TOKEN") == SECRET
    previous = connection.credential_reference
    await separate.store(database, {"API_TOKEN": "rotated"}, **scope(connection))
    await database.commit()
    assert await database.get(ConnectionCredential, previous) is None
    with await lease(database, separate, connection) as handle:
        assert handle.get("API_TOKEN") == "rotated"


@pytest.mark.asyncio
async def test_unbound_legacy_bundle_requires_explicit_reauthorization(database, broker):
    connection = await owner(database, broker)
    old = ConnectionCredential(encrypted_refresh_token="old-unbound", key_version="connector-v1")
    database.add(old)
    await database.flush()
    connection.credential_reference = old.id
    await database.commit()
    with pytest.raises(SecretBrokerError, match="reauthorization"):
        await lease(database, broker, connection)
    await broker.store(database, {"API_TOKEN": "new-owner-supplied"}, **scope(connection))
    await database.commit()
    assert (
        await database.get(ConnectionCredential, old.id)
    ).encrypted_refresh_token == "old-unbound"
    with await lease(database, broker, connection) as handle:
        assert handle.get("API_TOKEN") == "new-owner-supplied"


@pytest.mark.asyncio
async def test_broker_does_not_overwrite_or_delete_legacy_google_credentials(database, broker):
    connection = await owner(database, broker)
    google = ConnectionCredential(
        encrypted_refresh_token="legacy-google-ciphertext", key_version="local-v1"
    )
    database.add(google)
    await database.flush()
    connection.provider = "google"
    connection.config = {"legacy_bridge": True}
    connection.credential_reference = google.id
    await database.commit()
    with pytest.raises(SecretBrokerError):
        await broker.store(database, {"API_TOKEN": "new"}, **scope(connection))
    with pytest.raises(SecretBrokerError):
        await broker.delete_for_connection(database, **scope(connection))
    assert (
        await database.get(ConnectionCredential, google.id)
    ).encrypted_refresh_token == "legacy-google-ciphertext"


@pytest.mark.asyncio
async def test_delete_is_owner_scoped_idempotent_and_retains_shared_rows(database, broker):
    connection = await owner(database, broker)
    credential_id = connection.credential_reference
    anchor = Connection(
        user_id=connection.user_id,
        workspace_id=connection.workspace_id,
        provider="demo",
        external_account_id="provenance",
        credential_reference=credential_id,
    )
    database.add(anchor)
    await database.commit()
    await broker.delete_for_connection(database, **scope(connection))
    await broker.delete_for_connection(database, **scope(connection))
    await database.commit()
    assert connection.credential_reference is None
    assert anchor.credential_reference == credential_id
    assert await database.get(ConnectionCredential, credential_id) is not None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "values", [{}, {"bad name": SECRET}, {"API_TOKEN": ""}, {"API_TOKEN": "x" * 16385}]
)
async def test_rejected_store_does_not_change_existing_credentials(database, broker, values):
    connection = await owner(database, broker)
    credential_id = connection.credential_reference
    with pytest.raises(SecretBrokerError) as error:
        await broker.store(database, values, **scope(connection))
    assert SECRET not in str(error.value)
    assert connection.credential_reference == credential_id
