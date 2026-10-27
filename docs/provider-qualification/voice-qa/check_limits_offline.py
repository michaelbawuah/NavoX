"""Exercise continuation limits without importing ops or making any request."""

import http.cookiejar
import json
from pathlib import Path
import tempfile

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
print('{"offline_limits_and_registration_revocation": "pass", "fresh_prior_snapshot_and_single_use_run_id": "pass", "immutable_prior_scope_claim": "pass", "provider_calls": 0}')
