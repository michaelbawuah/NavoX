"""Read-only diagnosis of the pinned replacement stager; never stages or launches."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path("/srv/navox-vox-test")
DIRECTORY = ROOT / "browser/voice-qualification"
APP_SHA = "d21bf978df3c14d393fe05acf0fe41772841b23a"
QA_SHA = "83530395f83525a25563caa83ccbf2ce88a2a722"
RUN_ID = "voice-memory-20261027-b"
PROFILE = "post-startup-audit-three-calls"
STOPPED_SHA = "2442c73461a9b6fa76ef05f3fc17ec55f60765a24572f66e6c1db9e06a3bd5ef"
MAX_FILE = 256 * 1024
MAX_LOG = 2 * 1024 * 1024
PHASE = "initial"
BASELINE = {
    "run_on_isolated.py": "8192943ee88e918dcdb8a79fb2e566f6e9a830c94abf5f14a549f6779d11f6d5",
    "qualify.cjs": "7c625a55780cb271398b8fa2e642c13db617ff37566e19bfb642728cab5c90d8",
    "synthetic-mic.cjs": "1294647003d0832a16f1169ba915936ac96ff9d49fb9428430bd09693717065e",
    "fixtures.json": "cb4a58651d40d60b86c7551a07a4f84464f27580178907e45b6d67c6723c5e1c",
    "prepare_audio.py": "0cb57124de40bfc5b6b6860582cb90562e4c2231f3cded3afa654cbaa2a0f147",
    "check_limits_offline.py": "427f761489d1b3c9aa301d2b696734fb013dcb6eab6e5f1474a30f550d107316",
    "offline-probe.cjs": "cde150d842fb925b89b0a75a3ad24c3e9a444e7df864829fd5082fd747f4a1e7",
    "README.md": "2ba4713e49b058a506ee0751752459b925eac9cc08f6d89aa0d2df1e74682d47",
}
TARGET = {
    "run_on_isolated.py": "45a6258776ec5ea9cc4ee49abfa43f06ff1888cdeef4778330259e8fec0df32c",
    "qualify.cjs": "cf9572b4ea78fab2af14fd86a44c94c47f13751ee0043837c90988a683d1fc5e",
    "synthetic-mic.cjs": "54347c38004a8aa67f1159b356201954a05fb938269aff139d873ee7269610d5",
    "fixtures.json": BASELINE["fixtures.json"],
    "prepare_audio.py": BASELINE["prepare_audio.py"],
    "check_limits_offline.py": "93b45634086d0c1968e600b2db6dfdd583515f94528e8db6b297c98cd9d2af67",
    "offline-probe.cjs": "70ec38f2b9f43a188d499155f24614f90babb60a7e2c425e8fc87ff3d2a1f6ab",
    "README.md": "0b4e659b9c3f7a76a5391cb8e34cea4701f0fe1615ebfc14195e2730686c5c88",
}
PRIOR_CHECKS = (
    "read_only",
    "exact_calls",
    "call_count",
    "connected_ms",
    "known_provider_usd",
    "uncertainty",
    "reserved_tokens",
    "no_open_provider_streams",
    "no_old_active_calls",
    "no_unsettled_voice",
    "synthetic_owners",
    "prior_owner_allowance_sufficient",
    "ledger_fingerprint",
    "original_frozen_ledger",
    "latest_startup_call",
    "stopped_parent_claims",
)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True).encode()


def file_record(path, limit=MAX_FILE, *, read=True):
    """Never follow the final path component or include file content in a record."""
    try:
        info = path.lstat()
        kind = (
            "regular"
            if stat.S_ISREG(info.st_mode)
            else "symlink"
            if stat.S_ISLNK(info.st_mode)
            else "other"
        )
        record = {
            "present": True,
            "kind": kind,
            "size_bytes": info.st_size,
            "bounded": info.st_size <= limit,
        }
        data = None
        if read and kind == "regular" and record["bounded"]:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(fd, "rb") as stream:
                if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                    raise ValueError("Changed file kind")
                data = stream.read(limit + 1)
            if len(data) > limit:
                data = None
                record["bounded"] = False
            else:
                record["sha256"] = digest(data)
        return record, data
    except FileNotFoundError:
        return {"present": False, "kind": "missing", "bounded": False}, None
    except OSError:
        return {"present": True, "kind": "unreadable", "bounded": False}, None


def decoded(data):
    try:
        return json.loads(data) if data is not None else None
    except (ValueError, UnicodeError):
        return None


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def fetch_source(name):
    if name not in TARGET:
        raise ValueError("Fixed source filename required")
    url = (
        "https://raw.githubusercontent.com/michaelbawuah/NavoX/"
        + QA_SHA
        + "/docs/provider-qualification/voice-qa/"
        + name
    )
    with urllib.request.build_opener(NoRedirect()).open(
        urllib.request.Request(
            url,
            headers={
                "User-Agent": "NavoX-readonly-stage-inspection",
                "Accept": "application/octet-stream",
            },
        ),
        timeout=20,
    ) as response:
        if response.status != 200 or response.geturl() != url:
            return {"status": "response_mismatch", "matches_target": False}
        length = response.headers.get("Content-Length")
        if length is not None and (not length.isdecimal() or int(length) > MAX_FILE):
            return {"status": "length_rejected", "matches_target": False}
        data = response.read(MAX_FILE + 1)
    return {
        "status": "ok" if 0 < len(data) <= MAX_FILE else "size_rejected",
        "size_bytes": len(data),
        "sha256": digest(data),
        "matches_target": digest(data) == TARGET[name],
    }


def parent_preflight(directory, current, run):
    replacement = current == TARGET
    profile, connected, count, run_id = (
        (PROFILE, "65521", "3", RUN_ID)
        if replacement
        else ("fresh-three-calls", "48085", "2", "voice-memory-20261027-a")
    )
    command = [
        sys.executable,
        "-B",
        str(directory / "run_on_isolated.py"),
        "preflight",
        APP_SHA,
        "--profile",
        profile,
        "--prior-provider-usd",
        "0.0745244",
        "--prior-connected-ms",
        connected,
        "--prior-call-count",
        count,
        "--prior-uncertain-streams",
        "1",
        "--prior-reserved-tokens",
        "10525",
        "--qualification-run-id",
        run_id,
    ]
    result = {"status": "not_run", "profile": profile, "read_only": True}
    try:
        child = run(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=120,
        )
        result["returncode"] = child.returncode
        if len(child.stdout.encode()) > MAX_LOG:
            return {**result, "status": "oversized_output"}
        value = decoded(child.stdout)
        if not isinstance(value, dict):
            return {**result, "status": "invalid_json"}
        limits = value.get("qualification_limits")
        limits = limits if isinstance(limits, dict) else {}
        expected_limits = {
            "profile": profile,
            "qualification_run_id": run_id,
            "max_calls": 3,
            "prior_call_count": int(count),
            "total_call_ceiling": 6 if replacement else 5,
            "prior_connected_ms": int(connected),
            "prior_provider_usd": "0.0745244",
            "remaining_provider_usd": "1.9254756",
            "remaining_transport_ms": 117564 if replacement else 135000,
            "total_transport_ceiling_ms": 183085,
            "prior_uncertain_streams": 1,
            "prior_reserved_tokens": 10525,
            "financial_qualified": False,
        }
        checks = {
            "sha": value.get("sha") == APP_SHA,
            "app_source_sha": value.get("app_source_sha") == APP_SHA,
            "scope": value.get("scope") == "isolated-test",
            "readiness_only": value.get("readiness_only") is True,
            "provider_calls": value.get("provider_calls") == 0,
            "helper_sha256": value.get("helper_sha256") == current,
        }
        checks.update(
            {
                "limit_" + name: limits.get(name) == expected
                for name, expected in expected_limits.items()
            }
        )
        prior = value.get("prior_usage")
        prior = prior if isinstance(prior, dict) else {}
        prior_checks = prior.get("checks")
        prior_checks = prior_checks if isinstance(prior_checks, dict) else {}
        return {
            **result,
            "status": "parsed",
            "stager_contract_checks": checks,
            "ready": value.get("ready") is True,
            "prior_verified": prior.get("verified") is True,
            "run_id_unused": value.get("run_id_unused") is True,
            "scope_unused": value.get("scope_unused") is True,
            "prior_checks": {
                name: prior_checks.get(name) is True
                for name in PRIOR_CHECKS
                if name in prior_checks
            },
        }
    except subprocess.TimeoutExpired:
        return {**result, "status": "timeout"}
    except (OSError, ValueError, UnicodeError):
        return {**result, "status": "execution_error"}


def inspect_stage(
    root=ROOT, directory=DIRECTORY, *, fetch=fetch_source, run=subprocess.run
):
    global PHASE
    PHASE = "directory_resolution"
    resolved_root, resolved_directory = root.resolve(), directory.resolve()
    safe = (
        resolved_root == root
        and directory.is_dir()
        and not directory.is_symlink()
        and resolved_directory.is_relative_to(resolved_root)
    )
    exact_directory = safe and resolved_directory == directory
    paths = {
        "root_exists": root.exists(),
        "root_is_dir": root.is_dir(),
        "root_is_symlink": root.is_symlink(),
        "root_resolve_matches": resolved_root == root,
        "directory_exists": directory.exists(),
        "directory_is_dir": directory.is_dir(),
        "directory_is_symlink": directory.is_symlink(),
        "directory_resolve_matches": resolved_directory == directory,
        "browser_is_symlink": (root / "browser").is_symlink(),
        "resolved_within_root": resolved_directory.is_relative_to(resolved_root),
        "resolved_directory_sha256": digest(str(resolved_directory).encode()),
    }
    result = {
        "schema": "navox-readonly-replacement-stage-inspection.v1",
        "qa_commit_sha": QA_SHA,
        "run_id": RUN_ID,
        "read_only": True,
        "starts_or_authorizes_live_calls": False,
        "paths": paths,
    }
    downloads = {}
    PHASE = "pinned_downloads"
    for name in TARGET:
        try:
            downloads[name] = fetch(name)
        except urllib.error.HTTPError as error:
            downloads[name] = {
                "status": "http_error",
                "http_status": error.code,
                "matches_target": False,
            }
        except urllib.error.URLError:
            downloads[name] = {"status": "network_error", "matches_target": False}
        except Exception as error:  # noqa: BLE001 - no URL, response or private exception text
            downloads[name] = {
                "status": "fetch_error",
                "failure_class": type(error).__name__,
                "matches_target": False,
            }
    result["pinned_downloads"] = downloads
    result["pinned_downloads_total_bounded"] = (
        sum(value.get("size_bytes", 0) for value in downloads.values()) <= 1024 * 1024
    )
    if not safe:
        return {
            **result,
            "filesystem_status": "unsafe_directory_skipped",
            "parent_preflight": {"status": "not_run"},
            "first_observed_failed_guard": "directory_guard",
            "stager_valueerror_predicates": {"directory_guard": False},
        }
    PHASE = "replacement_markers"
    markers = {
        name: file_record(directory / name, MAX_LOG, read=False)[0]
        for name in (
            "run-" + RUN_ID + ".claim.json",
            "execution-" + RUN_ID + ".log",
            "launch-" + RUN_ID + ".json",
        )
    }
    scope = list(directory.glob("scope-" + PROFILE + "-*.claim.json"))
    markers["scope_claims"] = {
        "present": bool(scope),
        "count": len(scope),
        "entries": [file_record(path, MAX_FILE, read=False)[0] for path in scope[:8]],
    }
    no_markers = not any(item["present"] for item in markers.values())
    PHASE = "helper_fingerprints"
    current_records = {name: file_record(directory / name) for name in TARGET}
    current = {
        name: record.get("sha256") for name, (record, _) in current_records.items()
    }
    result["current_helpers"] = {
        name: {
            **record,
            "matches_baseline": current[name] == BASELINE[name],
            "matches_target": current[name] == TARGET[name],
        }
        for name, (record, _) in current_records.items()
    }
    PHASE = "stopped_report"
    _, raw = file_record(directory / "execution-voice-memory-20261027-a.log", MAX_LOG)
    objects = []
    for line in raw.splitlines() if raw is not None else []:
        value = decoded(line)
        if isinstance(value, dict):
            objects.append(value)
    stopped = objects[0] if len(objects) == 1 else {}
    stopped_checks = {
        "one_report": len(objects) == 1,
        "canonical_sha256_matches": digest(canonical(stopped)) == STOPPED_SHA,
        "app_sha_matches": stopped.get("sha") == APP_SHA,
        "run_id_matches": stopped.get("qualification_run_id")
        == "voice-memory-20261027-a",
        "failed": stopped.get("passed") is False,
        "baseline_helpers_match": isinstance(stopped.get("source_preflight"), dict)
        and stopped["source_preflight"].get("helper_sha256") == BASELINE,
    }
    PHASE = "manifest_backup"
    manifest_record, data = file_record(directory / ("stage-" + RUN_ID + ".json"))
    expected_manifest = {
        "schema": "navox-pinned-replacement-stage.v1",
        "qa_commit_sha": QA_SHA,
        "app_sha": APP_SHA,
        "run_id": RUN_ID,
        "profile": PROFILE,
        "baseline_helper_sha256": BASELINE,
        "helper_sha256": TARGET,
    }
    manifest = decoded(data)
    manifest_checks = {
        name: isinstance(manifest, dict) and manifest.get(name) == expected
        for name, expected in expected_manifest.items()
    }
    backup = directory / ("helper-backup-" + RUN_ID)
    backup_safe = (
        backup.is_dir()
        and not backup.is_symlink()
        and backup.resolve().is_relative_to(resolved_root)
    )
    backup_hashes = (
        {name: file_record(backup / name)[0].get("sha256") for name in BASELINE}
        if backup_safe
        else {}
    )
    _, backup_json = (
        file_record(backup / "baseline.json") if backup_safe else ({}, None)
    )
    backup_valid = backup_hashes == BASELINE
    expected_backup = {"app_sha": APP_SHA, "helper_sha256": BASELINE}
    stage_valid = manifest == expected_manifest and current == TARGET and backup_valid
    guards = [
        ("directory_guard", exact_directory),
        ("root_guard", resolved_root == root),
        ("replacement_scope_unused", no_markers),
        ("stopped_report", all(stopped_checks.values())),
        (
            "pinned_downloads",
            all(value.get("matches_target") is True for value in downloads.values()),
        ),
        (
            "staged_source_or_baseline",
            stage_valid if manifest_record["present"] else current == BASELINE,
        ),
    ]
    result.update(
        {
            "filesystem_status": "inspected",
            "replacement_markers": markers,
            "stopped_report_checks": stopped_checks,
            "stage_manifest": {
                **manifest_record,
                "exact_matches": manifest == expected_manifest,
                "checks": manifest_checks,
            },
            "backup": {
                "present": backup.exists(),
                "is_symlink": backup.is_symlink(),
                "exact_helpers": backup_valid,
                "baseline_json_matches": decoded(backup_json) == expected_backup,
            },
            "stager_valueerror_predicates": {name: passed for name, passed in guards},
            "first_observed_failed_guard": next(
                (name for name, passed in guards if not passed), "none_observed"
            ),
            "runtime_ops_guard_inspected": False,
        }
    )
    if all(passed for _, passed in guards) and current in (BASELINE, TARGET):
        PHASE = "readonly_parent_preflight"
        result["parent_preflight"] = parent_preflight(directory, current, run)
    else:
        result["parent_preflight"] = {"status": "not_run"}
    PHASE = "complete"
    result["inspection_phase"] = PHASE
    return result


if __name__ == "__main__":
    try:
        print(json.dumps(inspect_stage()))
    except Exception as error:  # noqa: BLE001 - fixed diagnostic envelope only
        print(
            json.dumps(
                {
                    "schema": "navox-readonly-replacement-stage-inspection.v1",
                    "run_id": RUN_ID,
                    "read_only": True,
                    "starts_or_authorizes_live_calls": False,
                    "inspection_failed": True,
                    "phase": PHASE,
                    "failure_class": type(error).__name__,
                }
            )
        )
        raise SystemExit(1)
