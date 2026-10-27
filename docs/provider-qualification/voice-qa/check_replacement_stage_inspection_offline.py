"""Read-only diagnostic regressions using controlled files and no external I/O."""

import copy
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

sys.dont_write_bytecode = True
import inspect_replacement_stage as helper

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
VERSIONS = {
    "baseline": "b51e82a9b175ec3f1a26fee12795753ce2f6ca47",
    "target": helper.QA_SHA,
}
SOURCES = {
    version: {
        name: subprocess.check_output(
            ["git", "show", sha + ":docs/provider-qualification/voice-qa/" + name],
            cwd=REPO,
        )
        for name in helper.TARGET
    }
    for version, sha in VERSIONS.items()
}
REPORT = json.loads((HERE / "results/voice-memory-20261027-a.json").read_text())[
    "supplied_report"
]
assert {
    name: helper.digest(data) for name, data in SOURCES["baseline"].items()
} == helper.BASELINE
assert {
    name: helper.digest(data) for name, data in SOURCES["target"].items()
} == helper.TARGET


def fixture(root, version="baseline"):
    directory = root / "browser/voice-qualification"
    directory.mkdir(parents=True)
    for name, data in SOURCES[version].items():
        (directory / name).write_bytes(data)
    (directory / "execution-voice-memory-20261027-a.log").write_text(
        json.dumps(REPORT) + "\n"
    )
    (directory / "run-voice-memory-20261027-a.claim.json").write_bytes(
        b"old-claim-must-stay"
    )
    (directory / "fixtures").mkdir()
    (directory / "fixtures/fixture.wav").write_bytes(b"old-audio-must-stay")
    return directory


def snapshot(root):
    return {
        str(path.relative_to(root)): (
            path.lstat().st_mtime_ns,
            path.read_bytes() if path.is_file() and not path.is_symlink() else None,
        )
        for path in root.rglob("*")
    }


def fetched(name):
    data = SOURCES["target"][name]
    return {
        "status": "ok",
        "size_bytes": len(data),
        "sha256": helper.digest(data),
        "matches_target": True,
    }


def never(*_args, **_kwargs):
    raise AssertionError("Unexpected execution or external I/O")


def parent(command, **kwargs):
    assert command[3:5] == ["preflight", helper.APP_SHA]
    assert not any("authorized" in item for item in command)
    assert kwargs == {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": True,
        "timeout": 120,
    }
    replacement = command[command.index("--profile") + 1] == helper.PROFILE
    current = helper.TARGET if replacement else helper.BASELINE
    limits = {
        "profile": helper.PROFILE if replacement else "fresh-three-calls",
        "qualification_run_id": helper.RUN_ID
        if replacement
        else "voice-memory-20261027-a",
        "max_calls": 3,
        "prior_call_count": 3 if replacement else 2,
        "total_call_ceiling": 6 if replacement else 5,
        "prior_connected_ms": 65521 if replacement else 48085,
        "prior_provider_usd": "0.0745244",
        "remaining_provider_usd": "1.9254756",
        "remaining_transport_ms": 117564 if replacement else 135000,
        "total_transport_ceiling_ms": 183085,
        "prior_uncertain_streams": 1,
        "prior_reserved_tokens": 10525,
        "financial_qualified": False,
    }
    value = {
        "sha": helper.APP_SHA,
        "app_source_sha": helper.APP_SHA,
        "scope": "isolated-test",
        "readiness_only": True,
        "provider_calls": 0,
        "helper_sha256": current,
        "qualification_limits": limits,
        "ready": replacement,
        "prior_usage": {
            "verified": True,
            "checks": {
                "read_only": True,
                "exact_calls": True,
                "private arbitrary data": "not echoed",
            },
        },
        "run_id_unused": replacement,
        "scope_unused": replacement,
        "private response": "not echoed",
    }
    return SimpleNamespace(
        returncode=0, stdout=json.dumps(value), stderr="private stderr not echoed"
    )


with tempfile.TemporaryDirectory() as temporary:
    base = Path(temporary)
    for version in VERSIONS:
        root = base / version
        directory = fixture(root, version)
        before = snapshot(root)
        result = helper.inspect_stage(root, directory, fetch=fetched, run=parent)
        assert snapshot(root) == before
        assert result["stager_valueerror_predicates"]["directory_guard"] is True
        assert all(result["stopped_report_checks"].values())
        assert result["parent_preflight"]["status"] == (
            "parsed" if version == "baseline" else "not_run"
        )
        if version == "baseline":
            assert all(result["parent_preflight"]["stager_contract_checks"].values())
            assert result["parent_preflight"]["ready"] is False
        assert "private" not in json.dumps(result)

    root = base / "staged"
    directory = fixture(root, "target")
    manifest = {
        "schema": "navox-pinned-replacement-stage.v1",
        "qa_commit_sha": helper.QA_SHA,
        "app_sha": helper.APP_SHA,
        "run_id": helper.RUN_ID,
        "profile": helper.PROFILE,
        "baseline_helper_sha256": helper.BASELINE,
        "helper_sha256": helper.TARGET,
    }
    manifest_path = directory / ("stage-" + helper.RUN_ID + ".json")
    manifest_path.write_text(json.dumps(manifest))
    backup = directory / ("helper-backup-" + helper.RUN_ID)
    backup.mkdir()
    for name, data in SOURCES["baseline"].items():
        (backup / name).write_bytes(data)
    (backup / "baseline.json").write_text(
        json.dumps({"app_sha": helper.APP_SHA, "helper_sha256": helper.BASELINE})
    )
    before = snapshot(root)
    valid = helper.inspect_stage(root, directory, fetch=fetched, run=parent)
    assert snapshot(root) == before
    assert valid["first_observed_failed_guard"] == "none_observed"
    assert valid["backup"]["baseline_json_matches"] is True
    assert valid["stage_manifest"]["exact_matches"] is True
    assert valid["parent_preflight"]["status"] == "parsed"
    assert valid["parent_preflight"]["ready"] is True
    assert all(valid["parent_preflight"]["stager_contract_checks"].values())
    changed = copy.deepcopy(manifest)
    changed["qa_commit_sha"] = "a" * 40
    manifest_path.write_text(json.dumps(changed))
    bad = helper.inspect_stage(root, directory, fetch=fetched, run=never)
    assert bad["stage_manifest"]["checks"]["qa_commit_sha"] is False
    assert bad["first_observed_failed_guard"] == "staged_source_or_baseline"
    manifest_path.write_text(json.dumps(manifest))
    (backup / "README.md").write_bytes(b"changed backup")
    bad = helper.inspect_stage(root, directory, fetch=fetched, run=never)
    assert bad["backup"]["exact_helpers"] is False
    assert bad["first_observed_failed_guard"] == "staged_source_or_baseline"

    for marker in (
        "run-" + helper.RUN_ID + ".claim.json",
        "execution-" + helper.RUN_ID + ".log",
        "launch-" + helper.RUN_ID + ".json",
        "scope-" + helper.PROFILE + "-abc.claim.json",
    ):
        root = base / marker
        directory = fixture(root)
        (directory / marker).write_bytes(b"marker must stay")
        before = snapshot(root)
        marked = helper.inspect_stage(root, directory, fetch=fetched, run=never)
        assert marked["first_observed_failed_guard"] == "replacement_scope_unused"
        assert marked["parent_preflight"]["status"] == "not_run"
        assert snapshot(root) == before

    root = base / "tampered"
    directory = fixture(root)
    (directory / "qualify.cjs").write_bytes(b"unverified helper")
    bad = helper.inspect_stage(root, directory, fetch=fetched, run=never)
    assert bad["current_helpers"]["qualify.cjs"]["matches_baseline"] is False
    assert bad["parent_preflight"]["status"] == "not_run"
    (directory / "execution-voice-memory-20261027-a.log").write_bytes(
        b'{"private content":"not echoed"}'
    )
    bad = helper.inspect_stage(root, directory, fetch=fetched, run=never)
    assert bad["stopped_report_checks"]["canonical_sha256_matches"] is False
    assert "private content" not in json.dumps(bad)

    # A browser symlink inside the root reproduces the stricter staging guard.
    root = base / "symlink"
    actual = root / "actual"
    actual.mkdir(parents=True)
    (root / "browser").symlink_to(actual, target_is_directory=True)
    directory = fixture(root)
    before = snapshot(root)
    linked = helper.inspect_stage(root, directory, fetch=fetched, run=never)
    assert linked["paths"]["browser_is_symlink"] is True
    assert linked["paths"]["directory_resolve_matches"] is False
    assert linked["first_observed_failed_guard"] == "directory_guard"
    assert snapshot(root) == before

    root = base / "contract"
    directory = fixture(root, "target")
    (directory / ("stage-" + helper.RUN_ID + ".json")).write_text(json.dumps(manifest))
    backup = directory / ("helper-backup-" + helper.RUN_ID)
    backup.mkdir()
    for name, data in SOURCES["baseline"].items():
        (backup / name).write_bytes(data)
    (backup / "baseline.json").write_text(
        json.dumps({"app_sha": helper.APP_SHA, "helper_sha256": helper.BASELINE})
    )

    def malformed(command, **kwargs):
        child = parent(command, **kwargs)
        value = json.loads(child.stdout)
        value["qualification_limits"]["remaining_provider_usd"] = "9"
        value["sha"] = "bad"
        child.stdout = json.dumps(value)
        return child

    bad = helper.inspect_stage(root, directory, fetch=fetched, run=malformed)
    checks = bad["parent_preflight"]["stager_contract_checks"]
    assert checks["sha"] is False and checks["limit_remaining_provider_usd"] is False
    assert bad["parent_preflight"]["status"] == "parsed"
    failed = helper.inspect_stage(
        root,
        directory,
        fetch=lambda name: {"status": "response_mismatch", "matches_target": False},
        run=parent,
    )
    assert failed["first_observed_failed_guard"] == "pinned_downloads"

assert (
    helper.NoRedirect().redirect_request(
        None, None, 302, "redirect", {}, "https://example.com"
    )
    is None
)
print(
    json.dumps(
        {
            "offline_replacement_stage_inspection": "pass",
            "files_bytes_and_mtimes_unchanged": True,
            "symlink_markers_manifest_backup_and_parent_contract_checks": True,
            "provider_calls": 0,
            "external_requests": 0,
        }
    )
)
