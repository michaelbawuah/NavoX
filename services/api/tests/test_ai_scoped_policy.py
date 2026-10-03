"""Scoped operator egress must not authorize another principal or task binding."""

from uuid import uuid4

import pytest
from test_ai_gateway_foundation import make_task

from navox.ai.foundation.contracts import (
    Profile,
    Provider,
    ProviderGrant,
    Sensitivity,
    TaskType,
    VersionedRef,
)
from navox.ai.routing import PolicyRules, RoutingTaskScope, intersect_policy


def scope_for(task, **changes):
    fields = (
        "workspace_id",
        "user_id",
        "task_type",
        "profile",
        "prompt",
        "output_schema",
        "sensitivity",
    )
    values = {field: getattr(task, field) for field in fields}
    values.update(changes)
    return RoutingTaskScope.model_validate(values)


def policy_for(task, scopes):
    return PolicyRules(
        grants=(
            ProviderGrant(provider=Provider.OPENAI, sensitivities=frozenset({task.sensitivity})),
        ),
        task_scopes=scopes,
    )


def test_exact_scope_preserves_only_existing_provider_grants():
    task = make_task()
    policy = intersect_policy(task, (policy_for(task, (scope_for(task),)),))
    assert policy.permits(Provider.OPENAI, task.sensitivity)
    assert not policy.permits(Provider.GEMINI, task.sensitivity)


@pytest.mark.parametrize(
    "field,value",
    [
        ("workspace_id", uuid4()),
        ("user_id", uuid4()),
        ("task_type", TaskType.PLAN),
        ("profile", Profile.PLANNING_HIGH),
        ("prompt", VersionedRef(name="different_prompt", version="v1")),
        ("output_schema", VersionedRef(name="different_schema", version="v1")),
        ("sensitivity", Sensitivity.SENSITIVE),
    ],
)
def test_each_scope_boundary_denies_egress(field, value):
    task = make_task()
    policy = intersect_policy(task, (policy_for(task, (scope_for(task, **{field: value}),)),))
    assert not policy.grants


def test_empty_scope_denies_and_omitted_scope_preserves_existing_policy():
    task = make_task()
    assert not intersect_policy(task, (policy_for(task, ()),)).grants
    assert intersect_policy(task, (policy_for(task, None),)).permits(
        Provider.OPENAI, task.sensitivity
    )


def test_allowlisted_task_cannot_restore_another_policy_denial():
    task = make_task()
    scoped = policy_for(task, (scope_for(task),))
    assert not intersect_policy(task, (scoped, PolicyRules())).grants
    assert not intersect_policy(task, (scoped, policy_for(task, ()))).grants
