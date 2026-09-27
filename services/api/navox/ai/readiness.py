"""Read-only acceptance inventory and catalog proposals. Never activates traffic.

Preparing a PERSONAL catalog is only a review artifact. Publication, policy
changes, provider evaluation, traffic changes and sends are separate operations.
"""

import argparse
import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from pydantic import AwareDatetime, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from navox.ai.catalog import catalog_template
from navox.ai.context import reject_credentials
from navox.ai.control import evaluation_passes
from navox.ai.domains import DOMAIN_TASKS, Domain, domain_reference
from navox.ai.features import configured_secrets
from navox.ai.foundation.contracts import Contract, Profile, Provider, Sensitivity, TaskType
from navox.ai.foundation.persistence import RegistryStore, canonical, digest, model_key
from navox.ai.foundation.registry import RegistrySnapshot, validate_registry_update
from navox.ai.prompts import COMMUNICATION_PROMPT_V2, EXTRACTION_PROMPT, EXTRACTION_SCHEMA
from navox.ai.routing import EvaluationEvidence, PolicyRules
from navox.ai.store import utc
from navox.core.settings import Settings
from navox.db.ai_registry import AIEvaluationRun, AIProfileAssignment, AIProviderHealth, AITaskRun
from navox.db.session import get_session_factory


def task_bindings() -> list[tuple[str, Profile, TaskType, str, str]]:
    bindings = [
        (
            profile.value.lower(),
            profile,
            TaskType.EXTRACT,
            f"{EXTRACTION_PROMPT.name}@{EXTRACTION_PROMPT.version}",
            f"{EXTRACTION_SCHEMA.name}@{EXTRACTION_SCHEMA.version}",
        )
        for profile in (Profile.EXTRACTION_FAST, Profile.EXTRACTION_HIGH_ACCURACY)
    ]
    bindings.append(
        (
            "communication",
            Profile.ASSISTANT_INTERACTIVE,
            TaskType.DRAFT_COMMUNICATION,
            f"{COMMUNICATION_PROMPT_V2.name}@{COMMUNICATION_PROMPT_V2.version}",
            "communication_draft@v1",
        )
    )
    bindings.extend(
        (domain.value, *DOMAIN_TASKS[domain], f"{domain.value}@v2", f"{domain.value}@v2")
        for domain in Domain
    )
    return bindings


def propose_catalog(
    current: RegistrySnapshot, *, personal_providers: frozenset[Provider] = frozenset()
) -> RegistrySnapshot:
    """Append exact shipped domain versions; preserve historical definitions."""
    if not personal_providers.issubset({m.reference.provider for m in current.models}):
        raise ValueError("Personal-data proposal includes an unregistered provider")
    template = catalog_template()
    references = {domain_reference(d) for d in Domain}
    prompts = list(current.prompts)
    schemas = list(current.schemas)
    for prompt in template.prompts:
        if prompt.reference not in references:
            continue
        old_prompt = next((d for d in prompts if d.reference == prompt.reference), None)
        if old_prompt is not None and old_prompt != prompt:
            raise ValueError("Published domain version differs from shipped contract")
        if old_prompt is None:
            prompts.append(prompt)
    for schema in template.schemas:
        if schema.reference not in references:
            continue
        old_schema = next((d for d in schemas if d.reference == schema.reference), None)
        if old_schema is not None and old_schema != schema:
            raise ValueError("Published domain version differs from shipped contract")
        if old_schema is None:
            schemas.append(schema)
    models = tuple(
        m.model_copy(
            update={"allowed_sensitivities": m.allowed_sensitivities | {Sensitivity.PERSONAL}}
        )
        if m.reference.provider in personal_providers
        else m
        for m in current.models
    )
    profiles = tuple(
        p.model_copy(
            update={
                "assignments": tuple(
                    dict.fromkeys(
                        (
                            *p.assignments,
                            *(
                                m.reference
                                for m in models
                                if m.enabled and p.required_capabilities <= m.capabilities
                            ),
                        )
                    )
                )
            }
        )
        if p.profile in {binding[0] for binding in DOMAIN_TASKS.values()}
        else p
        for p in current.profiles
    )
    proposed = RegistrySnapshot(
        revision=current.revision + 1,
        models=models,
        profiles=profiles,
        prompts=tuple(prompts),
        schemas=tuple(schemas),
    )
    validate_registry_update(current, proposed)
    if proposed.model_copy(update={"revision": current.revision}) == current:
        raise ValueError("Catalog already contains these exact domain bindings")
    return proposed


async def inventory(database: AsyncSession, settings: Settings) -> dict[str, Any]:
    registry = await RegistryStore(database).load()
    if registry is None:
        raise ValueError("No published catalog")
    policy = PolicyRules.model_validate(settings.ai_provider_policy)
    rows = []
    for name, profile, task_type, prompt, schema in task_bindings():
        definition = next((p for p in registry.profiles if p.profile == profile), None)
        for model in registry.models:
            key = model_key(model.reference)
            assignment = await database.get(AIProfileAssignment, (profile.value, key))
            health = await database.get(AIProviderHealth, key)
            evidence_row = await database.scalar(
                select(AIEvaluationRun)
                .where(
                    AIEvaluationRun.model_id == key,
                    AIEvaluationRun.model_digest == digest(canonical(model)),
                    AIEvaluationRun.registry_revision == registry.revision,
                    AIEvaluationRun.profile == profile.value,
                    AIEvaluationRun.task_type == task_type.value,
                    AIEvaluationRun.prompt == prompt,
                    AIEvaluationRun.schema == schema,
                )
                .order_by(AIEvaluationRun.evaluated_at.desc(), AIEvaluationRun.id.desc())
                .limit(1)
            )
            evidence = (
                EvaluationEvidence.model_validate_json(evidence_row.evidence)
                if evidence_row is not None
                else None
            )
            granted = model.allowed_sensitivities & next(
                (
                    grant.sensitivities
                    for grant in policy.grants
                    if grant.provider == model.reference.provider
                ),
                frozenset(),
            )
            bound = (
                definition is not None
                and model.reference in definition.assignments
                and task_type in definition.task_types
                and any(
                    f"{p.reference.name}@{p.reference.version}" == prompt
                    and f"{p.output_schema.name}@{p.output_schema.version}" == schema
                    for p in registry.prompts
                )
            )
            qualified = bool(
                bound
                and model.enabled
                and evidence is not None
                and evaluation_passes(evidence)
                and evidence_row is not None
                and utc(evidence_row.evaluated_at) == evidence.evaluated_at
            )
            rows.append(
                {
                    "task": name,
                    "profile": profile.value,
                    "model": key,
                    "prompt": prompt,
                    "schema": schema,
                    "binding_published": bound,
                    "evaluation_id": str(evidence_row.id) if evidence_row else None,
                    "evidence": evidence.model_dump(mode="json") if evidence else None,
                    "qualification_passes": qualified,
                    "operator_sensitivities": sorted(s.value for s in granted),
                    "traffic_percent": assignment.rollout_percent if assignment else 0,
                    "shadow": assignment.shadow_enabled if assignment else False,
                    "health": health.status if health else None,
                }
            )
    return {
        "registry_revision": registry.revision,
        "catalog_digest": digest(canonical(registry)),
        "generated_at": datetime.now(UTC).isoformat(),
        "automatic_mode": settings.ai_provider == "automatic",
        "bindings": rows,
        "workspace_user_policy_checked": False,
        "live_acceptance_complete": False,
        "note": (
            "Qualification is task-specific; serving also needs scope policy, health and rollout."
        ),
    }


class TraceManifest(Contract):
    workspace_id: UUID
    user_id: UUID
    started_at: AwareDatetime
    ended_at: AwareDatetime
    task_ids: tuple[UUID, ...] = Field(min_length=1, max_length=1000)


async def trace_coverage(database: AsyncSession, manifest: TraceManifest) -> dict[str, Any]:
    if (
        manifest.started_at > manifest.ended_at
        or manifest.ended_at > datetime.now(UTC)
        or len(set(manifest.task_ids)) != len(manifest.task_ids)
    ):
        raise ValueError("Invalid acceptance request manifest")
    rows = list(
        await database.scalars(
            select(AITaskRun).where(
                AITaskRun.workspace_id == manifest.workspace_id,
                AITaskRun.user_id == manifest.user_id,
                AITaskRun.task_id.in_(manifest.task_ids),
                AITaskRun.shadow.is_(False),
                AITaskRun.created_at >= manifest.started_at,
                AITaskRun.created_at <= manifest.ended_at,
            )
        )
    )
    present = {row.task_id for row in rows if row.trace_id is not None}
    terminal = {
        row.task_id for row in rows if row.status in {"COMPLETED", "FAILED"} and row.finished_at
    }
    missing = set(manifest.task_ids) - present
    fraction = len(present) / len(manifest.task_ids)
    return {
        "scope": "explicit acceptance task manifest, excluding shadow attempts",
        "expected_tasks": len(manifest.task_ids),
        "traced_tasks": len(present),
        "trace_coverage": fraction,
        "meets_99_percent_target": fraction >= 0.99,
        "missing_task_ids": sorted(str(i) for i in missing),
        "without_terminal_attempt": sorted(str(i) for i in set(manifest.task_ids) - terminal),
        "attempts": len(rows),
        "safety_or_quality_measured": False,
    }


async def run(args: argparse.Namespace) -> dict[str, Any]:
    settings = Settings()
    async with get_session_factory()() as database:
        if args.command == "trace-coverage":
            manifest = TraceManifest.model_validate_json(args.manifest.read_text(encoding="utf-8"))
            return await trace_coverage(database, manifest)
        report = await inventory(database, settings)
        if args.command == "prepare":
            current = await RegistryStore(database).load()
            assert current is not None
            assignments = list(await database.scalars(select(AIProfileAssignment)))
            if (
                current.revision != args.expected_revision
                or digest(canonical(current)) != args.expected_digest
                or any(a.rollout_percent or a.shadow_enabled for a in assignments)
            ):
                raise ValueError("Catalog or traffic differs from the reviewed checkpoint")
            proposed = propose_catalog(
                current, personal_providers=frozenset(args.personal_provider)
            )
            contents = {
                "current-catalog.json": canonical(current),
                "proposed-catalog.json": canonical(proposed),
                "readiness.json": json.dumps(report, sort_keys=True),
            }
            report = {
                "prepared_revision": proposed.revision,
                "catalog_digest": digest(canonical(proposed)),
                "model_personal_proposals": sorted(args.personal_provider),
                "published": False,
                "policy_changed": False,
                "traffic_changed": False,
                "prior_evidence_carries_forward": False,
            }
            contents["proposal-summary.json"] = json.dumps(report, sort_keys=True)
            for content in contents.values():
                reject_credentials(content, configured_secrets(settings))
            args.directory.mkdir(mode=0o700, parents=False, exist_ok=False)
            for name, content in contents.items():
                (args.directory / name).write_text(json.dumps(json.loads(content), indent=2) + "\n")
            return report
        return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("inventory")
    prepare = commands.add_parser("prepare")
    prepare.add_argument("--expected-revision", type=int, required=True)
    prepare.add_argument("--expected-digest", required=True)
    prepare.add_argument("--directory", type=Path, required=True)
    prepare.add_argument(
        "--personal-provider", type=Provider, choices=list(Provider), action="append", default=[]
    )
    coverage = commands.add_parser("trace-coverage")
    coverage.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(asyncio.run(run(args)), indent=2))
        return 0
    except Exception:
        print(json.dumps({"error": "readiness_check_rejected", "command": args.command}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
