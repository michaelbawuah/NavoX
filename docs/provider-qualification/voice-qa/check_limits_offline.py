"""Exercise continuation limits without importing ops or making any request."""

import ast
import contextlib
import copy
import http.cookiejar
import io
import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import run_on_isolated as helper
from run_on_isolated import qualification_limits

default = qualification_limits("full-three-calls")
assert default["max_calls"] == default["total_call_ceiling"] == 3
continued = qualification_limits("neutral-private-only", "0.125", 27800, 1)
assert continued["max_calls"] == 2
assert continued["max_calls"] + continued["prior_call_count"] == 3
assert continued["remaining_transport_ms"] + continued["prior_connected_ms"] == 135000
assert continued["remaining_provider_usd"] == "1.875"
fresh_args = ("fresh-three-calls", "0.0745244", 48085, 2, 1, 10525, "live-memory-20261027")
fresh = qualification_limits(*fresh_args)
assert fresh["max_calls"] == 3 and fresh["total_call_ceiling"] == 5
assert fresh["prior_call_count"] + fresh["max_calls"] == fresh["total_call_ceiling"]
assert fresh["remaining_transport_ms"] == 135000
assert fresh["remaining_transport_ms"] + fresh["prior_connected_ms"] == fresh["total_transport_ceiling_ms"] == 183085
assert fresh["remaining_provider_usd"] == "1.9254756"
assert fresh["prior_total_cost_usd"] is None and fresh["financial_qualified"] is False
assert fresh["admission_limit_ms"] == 65000 and fresh["media_call_limit_ms"] == 40000
assert fresh["settled_call_limit_ms"] == 45000

for args in (
    ("full-three-calls", "0", 1, 1),
    ("neutral-private-only",),
    ("neutral-private-only", "NaN", 1, 1),
    ("neutral-private-only", "Infinity", 1, 1),
    ("neutral-private-only", "-1", 1, 1),
    ("neutral-private-only", "2", 1, 1),
    ("neutral-private-only", "0", 0, 1),
    ("neutral-private-only", "0", 45001, 1),
    ("neutral-private-only", "0", 1.5, 1),
    ("neutral-private-only", "0", 1, 0),
    ("neutral-private-only", "0", 1, 2),
    ("full-three-calls", None, None, None, 1, 10525, "live-memory-20261027"),
    ("neutral-private-only", "0.125", 27800, 1, 1, 10525, "live-memory-20261027"),
    ("fresh-three-calls",),
    ("fresh-three-calls", "0", 48085, 2, 1, 10525, "live-memory-20261027"),
    ("fresh-three-calls", "NaN", 48085, 2, 1, 10525, "live-memory-20261027"),
    ("fresh-three-calls", "0.0745244", 0, 2, 1, 10525, "live-memory-20261027"),
    ("fresh-three-calls", "0.0745244", 48085, 0, 1, 10525, "live-memory-20261027"),
    ("fresh-three-calls", "0.0745244", 48085, 2, True, 10525, "live-memory-20261027"),
    ("fresh-three-calls", "0.0745244", 48085, 2, 0, 10525, "live-memory-20261027"),
    ("fresh-three-calls", "0.0745244", 48085, 2, 1, 0, "live-memory-20261027"),
    ("fresh-three-calls", "0.0745244", 48085, 2, 1, 10525, "../reuse-plan"),
    ("fresh-three-calls", "0.0745244", 48085, 2, 1, 10525, "UPPERCASE-ID"),
):
    try:
        qualification_limits(*args)
    except ValueError:
        continue
    raise AssertionError("Unsafe continuation input accepted")

prior = {
    "read_only": True, "call_count": 2, "call_hashes": list(helper.PRIOR_CALL_HASHES),
    "connected_ms": 48085, "known_provider_usd": "0.07452440000",
    "uncertain_provider_streams": 1, "unknown_provider_cost_components": 1,
    "reserved_tokens": 10525, "open_provider_streams": 0, "active_calls": 0,
    "reserved_voice_ms": 0, "uncertain_voice_ms": 0, "synthetic_owners_verified": True,
    "owner_budgets": [{"remaining_voice_ms": 3000000, "remaining_tokens": 200000}] * 2,
    "ledger_sha256": "a" * 64,
}
assert all(helper.prior_snapshot_checks(prior, fresh).values())
for change in (
    {"read_only": False}, {"call_hashes": ["b" * 64, helper.PRIOR_CALL_HASHES[1]]},
    {"call_count": 1}, {"known_provider_usd": "0"}, {"connected_ms": 0},
    {"uncertain_provider_streams": 0}, {"unknown_provider_cost_components": 0},
    {"reserved_tokens": 0}, {"open_provider_streams": 1}, {"active_calls": 1},
    {"reserved_voice_ms": 1}, {"uncertain_voice_ms": 1}, {"synthetic_owners_verified": False},
    {"owner_budgets": [{"remaining_voice_ms": 135000, "remaining_tokens": 39999}] * 2},
    {"owner_budgets": [{"remaining_voice_ms": 134999, "remaining_tokens": 40000}] * 2},
    {"owner_budgets": [None, None]}, {"ledger_sha256": "invalid"},
):
    assert not all(helper.prior_snapshot_checks({**prior, **change}, fresh).values())


class ReadOnlyOps:
    def run(self, command, *, text):
        assert text is True
        assert command[:5] == ["docker", "exec", "navox-vox-test-api-1", "/app/.venv/bin/python", "-B"]
        code = command[-1]
        compile(code, "prior-readonly-probe", "exec")
        assert "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY" in code
        assert "budget_status(" not in code and ".commit(" not in code
        assert "sha256(convert_to(call_id::text" in code
        return json.dumps(prior)


observed = helper.verify_prior_usage(ReadOnlyOps(), fresh)
assert observed["verified"] and observed["financial_qualified"] is False
assert observed["prior_total_cost_usd"] is None and observed["reserved_tokens"] == 10525
real_directory = helper.DIRECTORY
try:
    with tempfile.TemporaryDirectory() as directory:
        helper.DIRECTORY = Path(directory)
        claimed = helper.claim_run_id(fresh, "b" * 40, observed)
        value = json.loads(claimed.read_text())
        assert value["state"] == "claimed-before-signup"
        assert value["live_calls_may_have_started"] is False
        assert value["prior_ledger_sha256"] == prior["ledger_sha256"]
        scope_path = helper.scope_claim_path(fresh, observed)
        scope_before = scope_path.read_bytes()
        scope = json.loads(scope_before)
        assert scope["run_claim_filename"] == claimed.name
        assert scope["qualification_run_id"] == fresh["qualification_run_id"]
        assert scope["state"] == "admission-scope-consumed"
        assert "live_calls_may_have_started" not in scope
        try:
            helper.claim_run_id(fresh, "b" * 40, observed)
        except FileExistsError:
            pass
        else:
            raise AssertionError("An already claimed run ID was admitted again")
        renamed = {**fresh, "qualification_run_id": "live-memory-20261027-other"}
        assert helper.scope_claim_path(renamed, observed) == scope_path
        try:
            helper.claim_run_id(renamed, "b" * 40, observed)
        except FileExistsError:
            pass
        else:
            raise AssertionError("A new run ID bypassed the consumed historical scope")
        assert scope_path.read_bytes() == scope_before
        assert not (Path(directory) / "run-live-memory-20261027-other.claim.json").exists()
finally:
    helper.DIRECTORY = real_directory

replacement_args = (helper.REPLACEMENT_PROFILE, "0.0745244", 65521, 3, 1, 10525, "voice-memory-20261027-b")
replacement = qualification_limits(*replacement_args)
assert replacement["max_calls"] == 3 and replacement["total_call_ceiling"] == 6
assert replacement["prior_call_count"] + replacement["max_calls"] == 6
assert replacement["remaining_transport_ms"] == 117564
assert replacement["prior_connected_ms"] + replacement["remaining_transport_ms"] == 183085
assert replacement["remaining_provider_usd"] == "1.9254756"
assert replacement["financial_qualified"] is False and replacement["prior_total_cost_usd"] is None
for index, invalid in ((1, "0"), (2, 48085), (2, True), (3, 2), (4, 0), (5, 0), (6, "voice-memory-20261027-c")):
    args = list(replacement_args)
    args[index] = invalid
    try:
        qualification_limits(*args)
    except ValueError:
        pass
    else:
        raise AssertionError("Replacement baseline or run ID changed")
assert not helper.execution_authorized(helper.REPLACEMENT_PROFILE, True, False)
assert not helper.execution_authorized(helper.REPLACEMENT_PROFILE, False, True)
assert not helper.execution_authorized(helper.REPLACEMENT_PROFILE, False, False)
assert helper.execution_authorized(helper.REPLACEMENT_PROFILE, True, True)
assert helper.execution_authorized("fresh-three-calls", True, False)
assert not helper.execution_authorized("full-three-calls", True, True)
replacement_prior = {**copy.deepcopy(prior), "call_count": 3,
    "call_hashes": [*helper.PRIOR_CALL_HASHES, helper.STARTUP_CALL_SHA256],
    "connected_ms": 65521, "ledger_sha256": "c" * 64, "policy_revision": 52,
    "owner_budgets": [{"remaining_voice_ms": 117564, "remaining_tokens": 40000}] * 3,
    "original_ledger_sha256": helper.ORIGINAL_LEDGER_SHA256,
    "original_ledger_unchanged": True, "latest_startup_call_verified": True,
    "stopped_parent_claims_verified": True}
assert all(helper.prior_snapshot_checks(replacement_prior, replacement).values())
for change in ({"original_ledger_sha256": "d" * 64}, {"original_ledger_unchanged": False},
               {"latest_startup_call_verified": False}, {"stopped_parent_claims_verified": False},
               {"owner_budgets": [{"remaining_voice_ms": 117563, "remaining_tokens": 40000}] * 3},
               {"owner_budgets": [{"remaining_voice_ms": 117564, "remaining_tokens": 39999}] * 3},
               {"call_hashes": list(helper.PRIOR_CALL_HASHES)}, {"connected_ms": 48085},
               {"owner_budgets": replacement_prior["owner_budgets"][:2]}):
    assert not all(helper.prior_snapshot_checks({**replacement_prior, **change}, replacement).values())


class ReplacementReadOnlyOps:
    def run(self, command, *, text):
        assert text is True
        code = command[-1]
        compile(code, "three-call-readonly-probe", "exec")
        assert helper.STARTUP_CALL_SHA256 in code and helper.STARTUP_STREAM_SHA256 in code
        assert helper.STARTUP_OWNER_SHA256 in code and helper.STARTUP_WORKSPACE_SHA256 in code
        assert "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY" in code
        assert "row.status == \"open\"" in code and "row.sequence == 15" in code
        assert "UsageCounters(idle_ms=17436, connected_ms=17436)" in code
        assert "owner_streams" in code and "owner_calls" in code
        tree = ast.parse(code)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                assert node.func.attr not in ("commit", "flush", "delete", "add", "with_for_update")
        return json.dumps(replacement_prior)


real_directory = helper.DIRECTORY
try:
    with tempfile.TemporaryDirectory() as directory:
        helper.DIRECTORY = Path(directory)
        assert helper.stopped_parent_claims()["verified"] is False
        parent_run_path = helper.DIRECTORY / ("run-" + helper.STOPPED_RUN_ID + ".claim.json")
        parent_scope_path = helper.DIRECTORY / ("scope-fresh-three-calls-" + helper.ORIGINAL_LEDGER_SHA256 + ".claim.json")
        parent_run = {"qualification_run_id": helper.STOPPED_RUN_ID, "app_source_sha": helper.STOPPED_SOURCE_SHA,
            "prior_ledger_sha256": helper.ORIGINAL_LEDGER_SHA256, "scope_claim_filename": parent_scope_path.name,
            "state": "stopped", "functional_passed": False, "financial_qualified": False,
            "live_calls_may_have_started": True, "limits": {"profile": "fresh-three-calls"}}
        parent_scope = {"profile": "fresh-three-calls", "prior_ledger_sha256": helper.ORIGINAL_LEDGER_SHA256,
            "qualification_run_id": helper.STOPPED_RUN_ID, "app_source_sha": helper.STOPPED_SOURCE_SHA,
            "run_claim_filename": parent_run_path.name, "state": "admission-scope-consumed"}
        report = json.loads((Path(__file__).parent / "results/voice-memory-20261027-a.json").read_text())["supplied_report"]
        log_path = helper.DIRECTORY / ("execution-" + helper.STOPPED_RUN_ID + ".log")
        parent_run_path.write_text(json.dumps(parent_run))
        parent_scope_path.write_text(json.dumps(parent_scope))
        log_path.write_text(json.dumps(report) + "\n")
        parent_bytes = (parent_run_path.read_bytes(), parent_scope_path.read_bytes(), log_path.read_bytes())
        evidence = helper.stopped_parent_claims()
        assert evidence["verified"] is True
        for path, changed in ((parent_run_path, {**parent_run, "state": "completed"}),
                              (parent_scope_path, {**parent_scope, "qualification_run_id": "different"}),
                              (log_path, {**report, "passed": True})):
            before = path.read_bytes()
            path.write_text(json.dumps(changed))
            assert helper.stopped_parent_claims()["verified"] is False
            path.write_bytes(before)
        observed_replacement = helper.verify_prior_usage(ReplacementReadOnlyOps(), replacement)
        assert observed_replacement["verified"] is True
        assert observed_replacement["stopped_parent_evidence"] == evidence
        assert observed_replacement["prior_total_cost_usd"] is None
        assert observed_replacement["reserved_tokens"] == 10525
        claimed = helper.claim_run_id(replacement, "b" * 40, observed_replacement)
        scope_path = helper.scope_claim_path(replacement, observed_replacement)
        assert scope_path.name == "scope-post-startup-audit-three-calls-" + "c" * 64 + ".claim.json"
        assert json.loads(scope_path.read_text())["stopped_parent_evidence"] == evidence
        scope_before = scope_path.read_bytes()
        for changed in (replacement, {**replacement, "qualification_run_id": "voice-memory-20261027-c"}):
            try:
                helper.claim_run_id(changed, "b" * 40, observed_replacement)
            except (FileExistsError, ValueError):
                pass
            else:
                raise AssertionError("Replacement scope replay accepted")
        assert scope_path.read_bytes() == scope_before
        assert not (helper.DIRECTORY / "run-voice-memory-20261027-c.claim.json").exists()
        assert (parent_run_path.read_bytes(), parent_scope_path.read_bytes(), log_path.read_bytes()) == parent_bytes
finally:
    helper.DIRECTORY = real_directory

# Exercise actual CLI refusal before its claim/signup boundary, even with a
# fully ready read-only preflight. Neither preparation nor the old flag suffices.
real_argv, real_preflight, real_verify, real_claim = sys.argv, helper.preflight, helper.verify_prior_usage, helper.claim_run_id
old_ops = sys.modules.get("prepare_release")
real_directory = helper.DIRECTORY
try:
    with tempfile.TemporaryDirectory() as directory:
        helper.DIRECTORY = Path(directory)
        helper.preflight = lambda *_args: ({"ready": True, "readiness_only": True}, {})
        helper.verify_prior_usage = lambda *_args: {**replacement_prior, "verified": True}
        sys.modules["prepare_release"] = SimpleNamespace()
        def forbidden_claim(*_args):
            raise AssertionError("An unapproved replacement reached its claim")
        helper.claim_run_id = forbidden_claim
        base = ["run_on_isolated.py", "authorized-run", "b" * 40, "--profile", helper.REPLACEMENT_PROFILE,
            "--prior-provider-usd", "0.0745244", "--prior-connected-ms", "65521", "--prior-call-count", "3",
            "--prior-uncertain-streams", "1", "--prior-reserved-tokens", "10525", "--qualification-run-id", "voice-memory-20261027-b"]
        for flags in ([], ["--root-explicitly-authorized-live-provider-calls"], ["--root-explicitly-authorized-six-call-replacement-scope"]):
            sys.argv = base + flags
            capture = io.StringIO()
            with contextlib.redirect_stdout(capture):
                try:
                    helper.main()
                except SystemExit as error:
                    assert error.code == 2
                else:
                    raise AssertionError("Missing replacement approval accepted")
            assert json.loads(capture.getvalue())["execution_refused"] is True
        sys.argv = [*base[:1], "preflight", *base[2:]]
        capture = io.StringIO()
        with contextlib.redirect_stdout(capture):
            helper.main()
        assert json.loads(capture.getvalue())["readiness_only"] is True
        assert list(helper.DIRECTORY.iterdir()) == []
finally:
    sys.argv, helper.preflight, helper.verify_prior_usage, helper.claim_run_id = real_argv, real_preflight, real_verify, real_claim
    helper.DIRECTORY = real_directory
    if old_ops is None:
        sys.modules.pop("prepare_release", None)
    else:
        sys.modules["prepare_release"] = old_ops

jar = http.cookiejar.CookieJar()
jar.set_cookie(http.cookiejar.Cookie(0, "qa_fixture_cookie", "offline-marker", None, False,
                                    "127.0.0.1", False, False, "/", True, True, None,
                                    True, None, None, {}))
observed = []


def fake_api(_opener, resource, **options):
    assert options["loopback"] is True
    assert options["private_loopback_cookie"] == "qa_fixture_cookie=offline-marker"
    observed.append(resource)
    if resource == "/auth/logout":
        assert options["method"] == "POST"
        return 200, {"status": "signed_out"}
    assert resource == "/auth/me"
    return 401, None


real_api = helper.api
try:
    helper.api = fake_api
    assert helper.revoke_registration_session(None, jar) is True
finally:
    helper.api = real_api
assert observed == ["/auth/logout", "/auth/me"] and list(jar) == []
print('{"offline_limits_and_registration_revocation": "pass", "fresh_prior_snapshot_and_single_use_run_id": "pass", "immutable_prior_scope_claim": "pass", "replacement_three_call_snapshot_and_explicit_approval": "pass", "provider_calls": 0}')
