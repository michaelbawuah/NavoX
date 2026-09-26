"""Operator CLI for reviewed catalogs, policies, evaluation and staged rollout.

There is no public administration endpoint. Invoking these commands requires the
deployment's database access. Evaluation only sends the fixed synthetic corpus;
it never reads user sources, sends email, or changes a model's traffic share.
"""

import argparse
import asyncio
import json
from decimal import Decimal
from pathlib import Path
from typing import Any, cast
from uuid import UUID

from pydantic import TypeAdapter
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from navox.ai.catalog import catalog_template
from navox.ai.communication_evaluation import DraftingEvaluationReport, evaluate_communication
from navox.ai.configured import configured_adapters
from navox.ai.context import reject_credentials
from navox.ai.control import record_evaluation, record_healthy_probe, set_rollout, set_weights
from navox.ai.evaluation import EvaluationReport, evaluate_extraction
from navox.ai.features import configured_secrets
from navox.ai.foundation.contracts import Profile
from navox.ai.foundation.persistence import RegistryConflict, RegistryStore, canonical, digest
from navox.ai.foundation.registry import RegistrySnapshot
from navox.ai.routing import PolicyRules, RoutingWeights
from navox.core.settings import Settings
from navox.db.ai_registry import AIProfileAssignment, AIProviderHealth, AIRoutingPolicy
from navox.db.models import Workspace, WorkspaceMembership
from navox.db.session import get_session_factory


def read_file(path: Path) -> str:
    with path.open("rb") as source:
        raw = source.read(8_000_001)
    if len(raw) > 8_000_000:
        raise ValueError("Configuration exceeds the size limit")
    text = raw.decode("utf-8")
    reject_credentials(text)
    return text


async def publish_policy(
    database: AsyncSession,
    *,
    workspace_id: UUID,
    user_id: UUID | None,
    expected_revision: int,
    policy: PolicyRules,
) -> None:
    policy = PolicyRules.model_validate(policy)
    if expected_revision < 0 or await database.get(Workspace, workspace_id) is None:
        raise RegistryConflict("Workspace or revision is invalid")
    if (
        user_id is not None
        and await database.get(WorkspaceMembership, (workspace_id, user_id)) is None
    ):
        raise RegistryConflict("User is not a member of the workspace")
    scope = str(user_id) if user_id is not None else "workspace"
    if expected_revision == 0:
        if await database.get(AIRoutingPolicy, (workspace_id, scope)) is not None:
            raise RegistryConflict("Policy changed; reload before publishing")
        database.add(
            AIRoutingPolicy(
                workspace_id=workspace_id,
                scope_key=scope,
                revision=1,
                policy=policy.model_dump_json(),
            )
        )
    else:
        changed = await database.scalar(
            update(AIRoutingPolicy)
            .where(
                AIRoutingPolicy.workspace_id == workspace_id,
                AIRoutingPolicy.scope_key == scope,
                AIRoutingPolicy.revision == expected_revision,
            )
            .values(revision=expected_revision + 1, policy=policy.model_dump_json())
            .returning(AIRoutingPolicy.revision)
        )
        if changed is None:
            raise RegistryConflict("Policy changed; reload before publishing")
    await database.flush()


async def run(args: argparse.Namespace) -> dict[str, Any]:
    if args.command == "template":
        return cast(dict[str, Any], json.loads(canonical(catalog_template())))
    if (
        args.command == "evaluate"
        and args.corpus == "communication"
        and (args.output is None or args.profile != Profile.ASSISTANT_INTERACTIVE.value)
    ):
        raise ValueError("Draft evaluation requires ASSISTANT_INTERACTIVE and an output file")
    settings = Settings()
    factory = get_session_factory()
    async with factory() as database:
        if args.command == "publish":
            snapshot = RegistrySnapshot.model_validate_json(read_file(args.file))
            reject_credentials(canonical(snapshot), configured_secrets(settings))
            await RegistryStore(database).publish(
                snapshot, expected_revision=args.expected_revision
            )
            await database.commit()
            return {"published_revision": snapshot.revision, "traffic_enabled": False}
        if args.command == "policy":
            await publish_policy(
                database,
                workspace_id=args.workspace_id,
                user_id=args.user_id,
                expected_revision=args.expected_revision,
                policy=PolicyRules.model_validate_json(read_file(args.file)),
            )
            await database.commit()
            return {"policy_revision": args.expected_revision + 1}
        registry = await RegistryStore(database).load()
        if registry is None:
            raise ValueError("Publish a reviewed catalog first")
        if args.command == "status":
            assignments = (await database.scalars(select(AIProfileAssignment))).all()
            health = (await database.scalars(select(AIProviderHealth))).all()
            return {
                "registry_revision": registry.revision,
                "assignments": [
                    {
                        "model": a.model_id,
                        "profile": a.profile,
                        "percent": a.rollout_percent,
                        "shadow": a.shadow_enabled,
                    }
                    for a in assignments
                ],
                "health": [
                    {"model": h.model_id, "state": h.status, "error_code": h.error_code}
                    for h in health
                ],
            }
        if args.command == "record-evaluation":
            report: EvaluationReport | DraftingEvaluationReport = TypeAdapter(
                EvaluationReport | DraftingEvaluationReport
            ).validate_json(read_file(args.file))
            row = await record_evaluation(
                database,
                model_id=report.model_id,
                model_digest=report.model_digest,
                registry_revision=report.registry_revision,
                evidence=report.evidence(),
            )
            await database.commit()
            return {"evaluation_id": str(row.id), "traffic_promoted": False}
        if args.command == "rollout":
            await set_rollout(
                database,
                model_id=args.model,
                profile=Profile(args.profile),
                expected_revision=args.expected_revision,
                expected_percent=args.expected_percent,
                percent=args.percent,
                shadow=args.shadow,
            )
            await database.commit()
            return {"percent": args.percent, "shadow": args.shadow}
        if args.command == "weights":
            await set_weights(
                database,
                Profile(args.profile),
                RoutingWeights.model_validate_json(read_file(args.file)),
                expected_revision=args.expected_revision,
            )
            await database.commit()
            return {"weights_updated": True}
        if args.command in {"evaluate", "probe"}:
            if not args.live:
                raise ValueError("Use --live to explicitly authorize provider requests")
            model = next(
                (
                    m
                    for m in registry.models
                    if f"{m.reference.provider.value}:{m.reference.model}" == args.model
                ),
                None,
            )
            if model is None:
                raise ValueError("Model is not registered")
            adapters = configured_adapters(settings, registry)
            adapter = adapters.get(model.reference.provider)
            if adapter is None:
                raise ValueError("Provider credential is not configured")
            if args.command == "probe":
                available = await adapter.list_models()
                if not any(m.reference == model.reference for m in available):
                    raise ValueError("The provider did not confirm this model")
                await record_healthy_probe(
                    database,
                    model_id=args.model,
                    model_digest=digest(canonical(model)),
                    expected_revision=args.expected_revision,
                )
                await database.commit()
                return {"model": args.model, "health": "HEALTHY", "traffic_enabled": False}
            if args.corpus == "communication":
                draft_report = await evaluate_communication(
                    adapter=adapter,
                    registry=registry,
                    model=model,
                    policy=PolicyRules.model_validate(settings.ai_provider_policy),
                    max_cost=args.max_cost,
                    mode="live_provider",
                    secrets=configured_secrets(settings),
                )
                return draft_report.model_dump(mode="json")
            report = await evaluate_extraction(
                adapter=adapter,
                registry=registry,
                model=model,
                policy=PolicyRules.model_validate(settings.ai_provider_policy),
                profile=Profile(args.profile),
                max_cost=args.max_cost,
                mode="live_provider",
                secrets=configured_secrets(settings),
            )
            return report.model_dump(mode="json")
        raise ValueError("Unknown operator command")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in (
        "template",
        "status",
        "publish",
        "policy",
        "record-evaluation",
        "rollout",
        "weights",
        "evaluate",
        "probe",
    ):
        command = commands.add_parser(name)
        command.add_argument(
            "--output",
            type=Path,
            help="Write a JSON catalog/report; drafting reports include synthetic candidates",
        )
        if name in {"publish", "policy", "record-evaluation", "weights"}:
            command.add_argument("--file", type=Path, required=True)
        if name in {"publish", "policy", "rollout", "weights", "probe"}:
            command.add_argument("--expected-revision", type=int, required=True)
        if name in {"rollout", "weights", "evaluate"}:
            command.add_argument("--profile", choices=[p.value for p in Profile], required=True)
        if name in {"rollout", "evaluate", "probe"}:
            command.add_argument("--model", required=True, help="provider:exact-model-id")
        if name == "policy":
            command.add_argument("--workspace-id", type=UUID, required=True)
            command.add_argument("--user-id", type=UUID)
        if name == "rollout":
            command.add_argument("--expected-percent", type=int, required=True)
            command.add_argument("--percent", type=int, required=True)
            command.add_argument("--shadow", action="store_true")
        if name in {"evaluate", "probe"}:
            command.add_argument("--live", action="store_true")
        if name == "evaluate":
            command.add_argument(
                "--corpus", choices=["extraction", "communication"], default="extraction"
            )
            command.add_argument(
                "--max-cost",
                type=Decimal,
                required=True,
                help="USD budget for the complete synthetic corpus",
            )
    args = parser.parse_args(argv)
    try:
        report = asyncio.run(run(args))
        serialized = json.dumps(report, indent=2) + "\n"
        if args.output:
            args.output.write_text(serialized, encoding="utf-8")
        if report.get("report_type") == "communication_drafting":
            print(
                json.dumps(
                    {
                        "model_id": report["model_id"],
                        "cases": len(report["cases"]),
                        "schema_validated": sum(c["schema_validated"] for c in report["cases"]),
                        "requires_human_review": True,
                        "traffic_promoted": False,
                    }
                )
            )
            return 0 if all(c["schema_validated"] for c in report["cases"]) else 1
        print(serialized, end="")
        return (
            1 if args.command == "evaluate" and not all(c["passed"] for c in report["cases"]) else 0
        )
    except Exception:
        # Validation/provider/database exceptions can contain configuration or text.
        print(json.dumps({"error": "operator_command_rejected", "command": args.command}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
