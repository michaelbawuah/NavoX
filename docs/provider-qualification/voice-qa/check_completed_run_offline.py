"""Offline scope/read-only regression checks. Makes no external request."""

import ast
import copy
import json

import inspect_completed_run as helper


def fixture():
    return {
        "sha": helper.APP_SHA,
        "scope": "isolated-test",
        "qualification_run_id": helper.RUN_ID,
        "qa_owner_sha256": helper.OWNER_SHA,
        "qa_workspace_sha256": helper.WORKSPACE_SHA,
        "passed": False,
        "browser": {
            "failure_code": "call1:actual-owned-output-and-input-ready",
            "calls": [{"native_call_hash": helper.CALL_SHA, "turns": []}],
        },
    }


original = fixture()
assert helper.parse_report(json.dumps(original)) == original
assert (
    helper.parse_report("non-JSON terminal preamble\n" + json.dumps(original))
    == original
)
for mutate in (
    lambda value: value.update(sha="a" * 40),
    lambda value: value.update(scope="production"),
    lambda value: value.update(qualification_run_id="replacement-run"),
    lambda value: value.update(qa_owner_sha256="a" * 64),
    lambda value: value.update(qa_workspace_sha256="a" * 64),
    lambda value: value.update(passed=True),
    lambda value: value["browser"]["calls"].append(value["browser"]["calls"][0]),
    lambda value: value["browser"]["calls"][0].update(native_call_hash="b" * 64),
    lambda value: value["browser"]["calls"][0].update(turns=[{"fixture": "not-empty"}]),
    lambda value: value["browser"].update(failure_code="another-boundary"),
    lambda value: value.update(browser=[]),
    lambda value: value["browser"].update(calls={}),
    lambda value: value["browser"].update(calls=[None]),
):
    changed = copy.deepcopy(original)
    mutate(changed)
    try:
        helper.parse_report(json.dumps(changed))
    except ValueError:
        pass
    else:
        raise AssertionError("Differently scoped run accepted")
for raw in (
    "",
    json.dumps(original) + "\n" + json.dumps(original),
    "a" * (2 * 1024 * 1024 + 1),
):
    try:
        helper.parse_report(raw)
    except ValueError:
        pass
    else:
        raise AssertionError("Missing/ambiguous/oversized report accepted")

code = helper.probe_source()
compile(code, "read-only-container-probe", "exec")
tree = ast.parse(code)
statements = [
    node.args[0].value
    for node in ast.walk(tree)
    if isinstance(node, ast.Call)
    and isinstance(node.func, ast.Name)
    and node.func.id == "text"
    and node.args
    and isinstance(node.args[0], ast.Constant)
]
assert statements[:2] == [
    "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY",
    "SET LOCAL statement_timeout = '15000ms'",
]
for node in ast.walk(tree):
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        assert node.func.attr not in (
            "commit",
            "flush",
            "add",
            "delete",
            "with_for_update",
            "decrypt",
            "write_text",
            "write_bytes",
        )
assert "encrypted_transcript.is_not(None)" in code
assert "no-recorded-provider-streams" in code
assert "expected_native_call_observed" in code
assert "unexpected_additional_native_calls" in code
assert "reservation_status" in code
assert "if known_complete_costs else None" in code
assert "provider_unknown_or_incomplete_cost_components" in code
assert "re.fullmatch" in code and "Synthetic spoken voice QA" in code
assert (
    "getattr(row, name)" in code
)  # Same original ledger fields/fingerprint algorithm.


class FakeOps:
    PROJECT = helper.PROJECT

    def __init__(self):
        self.guards = 0
        self.calls = 0

    def guard(self):
        self.guards += 1

    def run(self, command, *, text):
        self.calls += 1
        assert text is True
        assert command[:6] == [
            "docker",
            "exec",
            "navox-vox-test-api-1",
            "/app/.venv/bin/python",
            "-B",
            "-c",
        ]
        assert command[6] == code
        return json.dumps(
            {
                "ok": True,
                "read_only": True,
                "recorded_provider_stream_count": 0,
                "provider_known_estimate_subtotal_usd": None,
            }
        )


ops = FakeOps()
# Avoid filesystem dependency while exercising the exact command path.
real_root = helper.ROOT
try:

    class Root:
        def resolve(self):
            return self

    helper.ROOT = Root()
    result = helper.audit(ops, original)
finally:
    helper.ROOT = real_root
assert ops.guards == ops.calls == 1
assert result["qualification_passed"] is False
assert result["audit"]["provider_known_estimate_subtotal_usd"] is None
assert result["run_id"] == helper.RUN_ID
print(
    json.dumps(
        {
            "offline_completed_run_audit": "pass",
            "scope_tamper_rejected": True,
            "read_only_transaction_checked": True,
            "provider_calls": 0,
        }
    )
)
