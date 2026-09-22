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
        "approvals",
        "proactive_preferences",
        "proactive_signals",
        "briefing_snapshots",
        "people",
        "person_identities",
        "operational_observations",
        "observation_evidence",
        "intelligence_feedback",
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


def test_approvals_bind_exact_actions_to_user_and_workspace() -> None:
    approval = Base.metadata.tables["approvals"]
    assert {foreign_key.target_fullname for foreign_key in approval.foreign_keys} == {
        "actions.id",
        "users.id",
        "workspaces.id",
    }


def test_proactive_tables_are_tenant_scoped_and_commitment_bound() -> None:
    preference = Base.metadata.tables["proactive_preferences"]
    signal = Base.metadata.tables["proactive_signals"]
    briefing = Base.metadata.tables["briefing_snapshots"]

    assert {foreign_key.target_fullname for foreign_key in preference.foreign_keys} == {
        "users.id",
        "workspaces.id",
    }
    assert {foreign_key.target_fullname for foreign_key in signal.foreign_keys} == {
        "commitments.id",
        "users.id",
        "workspaces.id",
    }
    assert {foreign_key.target_fullname for foreign_key in briefing.foreign_keys} == {
        "users.id",
        "workspaces.id",
    }


def test_spec_002_people_and_identities_are_workspace_scoped() -> None:
    person = Base.metadata.tables["people"]
    identity = Base.metadata.tables["person_identities"]

    assert {foreign_key.target_fullname for foreign_key in person.foreign_keys} == {"workspaces.id"}
    assert {foreign_key.target_fullname for foreign_key in identity.foreign_keys} == {
        "people.id",
        "workspaces.id",
    }

    unique_columns = {
        tuple(column.name for column in constraint.columns)
        for constraint in identity.constraints
        if constraint.__class__.__name__ == "UniqueConstraint"
    }
    assert ("workspace_id", "identity_type", "identity_value") in unique_columns


def test_spec_002_observations_preserve_tenant_scope_and_person_links() -> None:
    observation = Base.metadata.tables["operational_observations"]

    assert {foreign_key.target_fullname for foreign_key in observation.foreign_keys} == {
        "people.id",
        "users.id",
        "workspaces.id",
    }
    assert observation.c.confidence.type.precision == 4
    assert observation.c.confidence.type.scale == 3
    assert observation.c.extractor_version.nullable is False


def test_spec_002_evidence_is_bound_to_observation_and_authorized_connection() -> None:
    evidence = Base.metadata.tables["observation_evidence"]

    assert {foreign_key.target_fullname for foreign_key in evidence.foreign_keys} == {
        "connections.id",
        "operational_observations.id",
    }
    unique_columns = {
        tuple(column.name for column in constraint.columns)
        for constraint in evidence.constraints
        if constraint.__class__.__name__ == "UniqueConstraint"
    }
    assert (
        "observation_id",
        "connection_id",
        "provider",
        "source_type",
        "external_resource_id",
        "source_hash",
    ) in unique_columns


def test_spec_002_feedback_is_scoped_but_has_no_execution_authority_foreign_key() -> None:
    feedback = Base.metadata.tables["intelligence_feedback"]

    assert {foreign_key.target_fullname for foreign_key in feedback.foreign_keys} == {
        "users.id",
        "workspaces.id",
    }
    assert "action_id" not in feedback.c
    assert "approval_id" not in feedback.c
    assert "permission" not in feedback.c
