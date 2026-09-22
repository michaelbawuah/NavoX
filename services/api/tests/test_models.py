from navox.db import models  # noqa: F401 - register models before assertion
from navox.db.base import Base


def test_tenant_foundation_tables_are_registered() -> None:
    assert {
        "users",
        "workspaces",
        "workspace_memberships",
        "user_sessions",
        "connection_credentials",
        "connections",
        "oauth_authorization_attempts",
        "provider_event_subscriptions",
        "incoming_events",
        "objectives",
        "commitments",
        "commitment_sources",
        "commitment_relations",
        "plans",
        "plan_steps",
        "actions",
        "workflow_refs",
        "audit_events",
    } <= set(Base.metadata.tables)


def test_membership_has_workspace_and_user_foreign_keys() -> None:
    membership = Base.metadata.tables["workspace_memberships"]
    foreign_keys = {foreign_key.target_fullname for foreign_key in membership.foreign_keys}

    assert foreign_keys == {"users.id", "workspaces.id"}


def test_session_has_a_user_foreign_key() -> None:
    session = Base.metadata.tables["user_sessions"]
    foreign_keys = {foreign_key.target_fullname for foreign_key in session.foreign_keys}

    assert foreign_keys == {"users.id"}


def test_connection_is_scoped_to_a_user_and_workspace() -> None:
    connection = Base.metadata.tables["connections"]
    foreign_keys = {foreign_key.target_fullname for foreign_key in connection.foreign_keys}

    assert {"users.id", "workspaces.id", "connection_credentials.id"} == foreign_keys


def test_incoming_event_is_scoped_to_its_authenticated_connection() -> None:
    event = Base.metadata.tables["incoming_events"]
    foreign_keys = {foreign_key.target_fullname for foreign_key in event.foreign_keys}

    assert foreign_keys == {"connections.id", "users.id", "workspaces.id"}


def test_commitment_graph_tables_preserve_tenant_scope_and_provenance() -> None:
    commitment = Base.metadata.tables["commitments"]
    source = Base.metadata.tables["commitment_sources"]
    relation = Base.metadata.tables["commitment_relations"]

    assert {foreign_key.target_fullname for foreign_key in commitment.foreign_keys} == {
        "objectives.id",
        "users.id",
        "workspaces.id",
    }
    assert {foreign_key.target_fullname for foreign_key in source.foreign_keys} == {
        "commitments.id",
        "connections.id",
        "incoming_events.id",
    }
    assert {foreign_key.target_fullname for foreign_key in relation.foreign_keys} == {
        "commitments.id"
    }


def test_agent_execution_tables_are_tenant_scoped_and_persisted() -> None:
    plan = Base.metadata.tables["plans"]
    action = Base.metadata.tables["actions"]
    workflow_ref = Base.metadata.tables["workflow_refs"]
    audit_event = Base.metadata.tables["audit_events"]

    assert {foreign_key.target_fullname for foreign_key in plan.foreign_keys} == {
        "commitments.id",
        "objectives.id",
        "users.id",
        "workspaces.id",
    }
    assert {foreign_key.target_fullname for foreign_key in action.foreign_keys} == {
        "commitments.id",
        "plan_steps.id",
        "users.id",
        "workspaces.id",
    }
    assert {foreign_key.target_fullname for foreign_key in workflow_ref.foreign_keys} == {
        "users.id",
        "workspaces.id",
    }
    assert {foreign_key.target_fullname for foreign_key in audit_event.foreign_keys} == {
        "users.id",
        "workspaces.id",
    }
