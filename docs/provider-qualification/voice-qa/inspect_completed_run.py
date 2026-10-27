"""Read-only audit of the one stopped isolated synthetic qualification.

Run on the existing test host through stdin. This script neither authenticates to
NavoX nor invokes providers, removes files, changes claims, or mutates the ledger.
Only hashes, fixed enum values, booleans, and numeric usage/counts are emitted.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

sys.dont_write_bytecode = True

ROOT = Path("/srv/navox-vox-test")
PROJECT = "navox-vox-test"
RUN_ID = "voice-memory-20261027-a"
APP_SHA = "d21bf978df3c14d393fe05acf0fe41772841b23a"
OWNER_SHA = "5cb81d78d1db5cba24be5cc18ca1a3c8112e9dc01f6c040ad8e48b5b88690301"
WORKSPACE_SHA = "366db296f459ee53e8821561bdbfb90c6e0e926bcbe7f759d5014f7648eba723"
CALL_SHA = "67c962125e8bc2c65e4cecbdbd20b0594007c3b20fa6da2363bfb41daa07f706"
PRIOR_HASHES = (
    "6610556071cc0879c0c9dcc1b6e066af769c19c7fb5444c2a663f26e08dfd698",
    "4ee683777b39bcbff366aae873a90a07ad69e9cc5788857fdeb812a4066e3bc6",
)
PRIOR_LEDGER = "5240f4cc1ce0d03391dad6edfef1acbd9732733fd48f31eb2ae963e583c6423a"
LOG = ROOT / "browser/voice-qualification" / ("execution-" + RUN_ID + ".log")


def parse_report(raw):
    """Reject missing, ambiguous, or differently scoped execution reports."""
    if not isinstance(raw, str) or len(raw.encode()) > 2 * 1024 * 1024:
        raise ValueError("Oversized run report")
    objects = []
    for line in raw.splitlines():
        try:
            value = json.loads(line)
        except ValueError:
            continue
        if isinstance(value, dict):
            objects.append(value)
    if len(objects) != 1:
        raise ValueError("One execution report required")
    result = objects[0]
    if not (
        result.get("sha") == APP_SHA
        and result.get("scope") == "isolated-test"
        and result.get("qualification_run_id") == RUN_ID
        and result.get("qa_owner_sha256") == OWNER_SHA
        and result.get("qa_workspace_sha256") == WORKSPACE_SHA
        and result.get("passed") is False
    ):
        raise ValueError("Unexpected run identity")
    browser = result.get("browser", {})
    if not isinstance(browser, dict):
        raise ValueError("Unexpected browser report")  # noqa: TRY004 - invalid serialized report
    calls = browser.get("calls", [])
    if not (
        isinstance(calls, list)
        and len(calls) == 1
        and isinstance(calls[0], dict)
        and calls[0].get("native_call_hash") == CALL_SHA
        and calls[0].get("turns") == []
        and browser.get("failure_code") == "call1:actual-owned-output-and-input-ready"
    ):
        raise ValueError("Unexpected completed run boundary")
    return result


def probe_source():
    """Produce the in-container probe without importing runtime app code locally."""
    constants = repr(
        {
            "owner": OWNER_SHA,
            "workspace": WORKSPACE_SHA,
            "call": CALL_SHA,
            "prior_calls": PRIOR_HASHES,
            "prior_ledger": PRIOR_LEDGER,
        }
    )
    return (
        "EXPECTED = "
        + constants
        + "\n"
        + r"""
import asyncio, hashlib, json, re
from decimal import Decimal
from sqlalchemy import func, select, text
from navox.connectors.sync_state import database_now
from navox.db.models import User, UserSession, WorkspaceMembership
from navox.db.conversation_memory import MemoryConversation, MemoryFact, MemoryLearningJob, MemoryPrivacy, MemoryThread
from navox.db.memory import OwnerMemory
from navox.db.session import get_database_engine, get_session_factory
from navox.db.vox import VoxCall
from navox.db.vox_usage import VoxUsageStream, VoxVoiceCredit
from navox.vox import usage
from navox.vox.usage_schemas import UsageCounters

def digest(value):
    return hashlib.sha256(str(value).encode()).hexdigest()

def enum(value, choices):
    return value if value in choices else None if value is None else "other"

async def main():
    phase = "read-only-transaction"
    try:
        async with get_session_factory()() as database:
            await database.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
            await database.execute(text("SET LOCAL statement_timeout = '15000ms'"))
            phase = "synthetic-owner-guard"
            users = list((await database.execute(select(User.id, User.display_name, User.email).where(text(
                "encode(sha256(convert_to(users.id::text,'UTF8')),'hex') = :owner"
            )).params(owner=EXPECTED["owner"]))).all())
            if len(users) != 1:
                raise ValueError("Unexpected synthetic owner")
            user = users[0]
            if not (user.display_name == "Synthetic spoken voice QA" and
                    re.fullmatch(r"voice-qualified-[a-f0-9]{24}@example\.com", user.email)):
                raise ValueError("Synthetic owner guard rejected")
            memberships = list(await database.scalars(select(WorkspaceMembership).where(
                WorkspaceMembership.user_id == user.id, text(
                    "encode(sha256(convert_to(workspace_memberships.workspace_id::text,'UTF8')),'hex') = :workspace"
                )).params(workspace=EXPECTED["workspace"])))
            if len(memberships) != 1:
                raise ValueError("Unexpected synthetic workspace")
            workspace = memberships[0].workspace_id
            if digest(user.id) != EXPECTED["owner"] or digest(workspace) != EXPECTED["workspace"]:
                raise ValueError("Hash guard rejected")
            def owned(model):
                return (model.user_id == user.id, model.workspace_id == workspace)
            async def count(model, *extra):
                return await database.scalar(select(func.count()).select_from(model).where(*owned(model), *extra))
            now = await database_now(database)
            phase = "fresh-usage-snapshot"
            calls = list(await database.scalars(select(VoxCall).where(*owned(VoxCall)).order_by(VoxCall.created_at)))
            streams = list(await database.scalars(select(VoxUsageStream).where(*owned(VoxUsageStream)).order_by(VoxUsageStream.id)))
            call_ids = {row.id for row in calls} | {row.call_id for row in streams}
            if len(call_ids) > 3:
                raise ValueError("Unexpected fresh call count")
            expected_found = EXPECTED["call"] in {digest(value) for value in call_ids}
            new_calls = []
            for call_id in sorted(call_ids, key=str):
                totals = await usage.call_totals(database, user.id, workspace, call_id)
                call_streams = [row for row in streams if row.call_id == call_id]
                numeric = {key: getattr(totals, key) for key in (
                    "speaking_ms", "idle_ms", "connected_ms", "tokens", "reserved_tokens",
                    "reserved_voice_ms", "uncertain_voice_ms", "unknown_cost_components")}
                call_row = next((row for row in calls if row.id == call_id), None)
                new_calls.append({"native_call_sha256": digest(call_id), "stream_count": len(call_streams),
                    "reservation_row_present": call_row is not None,
                    "reservation_status": enum(call_row.status, ("reserved", "ended")) if call_row is not None else None,
                    "recorded_totals": numeric,
                    "recorded_known_estimate_usd": str(totals.known_estimated_cost_usd) if call_streams else None,
                    "recorded_total_estimate_usd": str(totals.estimated_cost_usd) if call_streams and totals.estimated_cost_usd is not None else None,
                    "cost_basis": totals.cost_basis if call_streams else "no-recorded-streams"})
            safe_streams = []
            for row in streams:
                counters = UsageCounters.model_validate(row.cumulative)
                estimated = usage.estimate(counters, row.pricing, row.kind)
                complete_estimate = row.status == "complete" and estimated is not None
                safe_streams.append({"stream_sha256": digest(row.id), "native_call_sha256": digest(row.call_id),
                    "kind": enum(row.kind, ("transport", "provider")),
                    "status": enum(row.status, ("open", "complete", "uncertain")),
                    "provider": enum(row.provider, ("openai",)),
                    "model": enum(row.model, ("gpt-realtime-2.1", "gpt-5.6-luna", "gpt-transcribe", "gpt-4o-mini-tts-2025-12-15")),
                    "model_sha256": digest(row.model) if row.model else None,
                    "sequence": row.sequence, "counters": counters.model_dump(),
                    "reserved_tokens": row.reserved_tokens,
                    "unsettled_token_hold": max(0, row.reserved_tokens-counters.total_tokens) if row.status != "complete" else 0,
                    "pricing_present": row.pricing is not None,
                    "pricing_sha256": hashlib.sha256(json.dumps(row.pricing, sort_keys=True).encode()).hexdigest() if row.pricing is not None else None,
                    "complete_known_estimate_usd": str(estimated) if complete_estimate else None,
                    "reconciled_cost_usd": str(row.reconciled_cost_usd) if row.reconciled_cost_usd is not None else None})
            phase = "erasure-session-counts"
            privacy = await database.scalar(select(MemoryPrivacy).where(*owned(MemoryPrivacy)))
            cleanup = {
                "memory_fact_rows": await count(MemoryFact), "memory_thread_rows": await count(MemoryThread),
                "owner_memory_rows": await count(OwnerMemory), "learning_job_rows": await count(MemoryLearningJob),
                "learning_result_ciphertexts": await count(MemoryLearningJob, MemoryLearningJob.encrypted_result.is_not(None)),
                "conversation_rows": await count(MemoryConversation),
                "conversation_ciphertext_rows": await count(MemoryConversation, (MemoryConversation.encrypted_transcript.is_not(None) | MemoryConversation.encrypted_summary.is_not(None))),
                "active_calls": await count(VoxCall, VoxCall.status == "reserved"),
                "active_sessions": await database.scalar(select(func.count()).select_from(UserSession).where(UserSession.user_id == user.id, UserSession.revoked_at.is_(None), UserSession.expires_at > now)),
                "unsettled_voice_credits": await count(VoxVoiceCredit, VoxVoiceCredit.settled_at.is_(None)),
                "history_disabled": privacy is not None and privacy.history_enabled is False,
                "off_the_record": privacy is not None and privacy.off_the_record is True,
            }
            phase = "historical-ledger-fingerprint"
            prior = list(await database.scalars(select(VoxUsageStream).where(text(
                "encode(sha256(convert_to(call_id::text,'UTF8')),'hex') IN (:first,:second)"
            )).params(first=EXPECTED["prior_calls"][0], second=EXPECTED["prior_calls"][1]).order_by(VoxUsageStream.id)))
            fingerprints = [{name: getattr(row, name) for name in (
                "id", "user_id", "workspace_id", "call_id", "kind", "provider", "model", "status", "sequence", "cumulative", "reserved_tokens", "pricing", "created_at", "observed_at", "reconciled_cost_usd", "reconciliation_id")}
                for row in prior]
            ledger = hashlib.sha256(json.dumps(fingerprints, sort_keys=True, default=str).encode()).hexdigest()
            prior_calls = sorted({digest(row.call_id) for row in prior})
            provider = [row for row in streams if row.kind == "provider"]
            transport = [row for row in streams if row.kind == "transport"]
            provider_costs = [(row, usage.estimate(UsageCounters.model_validate(row.cumulative), row.pricing, row.kind)) for row in provider]
            known_complete_costs = [amount for row, amount in provider_costs if row.status == "complete" and amount is not None]
            print(json.dumps({"ok": True, "read_only": True,
                "synthetic_owner_verified": True, "expected_native_call_observed": expected_found,
                "new_call_count": len(call_ids), "unexpected_additional_native_calls": len(call_ids) > 1,
                "new_calls": new_calls, "new_streams": safe_streams,
                "recorded_provider_stream_count": len(provider),
                "provider_cost_observation": "recorded-streams" if provider else "no-recorded-provider-streams",
                "provider_known_estimate_subtotal_usd": str(sum(known_complete_costs, Decimal(0))) if known_complete_costs else None,
                "provider_unknown_or_incomplete_cost_components": sum(row.status != "complete" or amount is None for row, amount in provider_costs),
                "open_provider_streams": sum(row.status == "open" for row in provider),
                "uncertain_provider_streams": sum(row.status == "uncertain" for row in provider),
                "transport_stream_count": len(transport),
                "transport_complete": bool(transport) and all(row.status == "complete" for row in transport),
                "cleanup_counts": cleanup,
                "prior_ledger_sha256": ledger,
                "prior_call_hashes": prior_calls,
                "prior_uncertain_provider_streams": sum(row.kind == "provider" and row.status == "uncertain" for row in prior),
                "prior_unsettled_token_hold": sum(max(0, row.reserved_tokens - UsageCounters.model_validate(row.cumulative).total_tokens) for row in prior if row.status != "complete"),
                "prior_ledger_unchanged": ledger == EXPECTED["prior_ledger"] and prior_calls == sorted(EXPECTED["prior_calls"]),
                "financial_qualified": False,
                "starts_or_authorizes_live_calls": False}))
    except Exception as error:
        print(json.dumps({"ok": False, "read_only": True, "phase": phase, "failure_class": type(error).__name__, "starts_or_authorizes_live_calls": False}))
    finally:
        await get_database_engine().dispose()
asyncio.run(main())
"""
    )


def audit(ops, report):
    """Only the existing isolated container receives this nonsecret read probe."""
    parse_report(json.dumps(report))
    ops.guard()
    if ROOT.resolve() != ROOT or ops.PROJECT != PROJECT:
        raise RuntimeError("Unexpected isolated project")
    output = ops.run(
        [
            "docker",
            "exec",
            PROJECT + "-api-1",
            "/app/.venv/bin/python",
            "-B",
            "-c",
            probe_source(),
        ],
        text=True,
    )
    if not isinstance(output, str) or len(output.encode()) > 2 * 1024 * 1024:
        raise ValueError("Oversized audit result")
    result = json.loads(output)
    if not isinstance(result, dict) or result.get("read_only") is not True:
        raise ValueError("Read-only result missing")
    return {
        "schema": "navox-stopped-voice-run-readonly-audit.v1",
        "run_id": RUN_ID,
        "expected_app_sha": APP_SHA,
        "execution_report_sha256": hashlib.sha256(
            json.dumps(report, sort_keys=True).encode()
        ).hexdigest(),
        "qualification_passed": False,
        "audit": result,
    }


def main():
    phase = "execution-log-read"
    try:
        if (
            LOG.is_symlink()
            or not LOG.is_file()
            or LOG.stat().st_size > 2 * 1024 * 1024
        ):
            raise ValueError("Expected bounded regular log")
        report = parse_report(LOG.read_text())
        phase = "isolated-ops-load"
        sys.path.insert(0, str(ROOT / "ops"))
        import prepare_release as ops

        phase = "read-only-audit"
        result = audit(ops, report)
        print(json.dumps(result))
        return 0 if result["audit"].get("ok") is True else 1
    except Exception as error:  # noqa: BLE001 - never expose private runtime error details
        print(
            json.dumps(
                {
                    "ok": False,
                    "read_only": True,
                    "phase": phase,
                    "failure_class": type(error).__name__,
                    "starts_or_authorizes_live_calls": False,
                }
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
