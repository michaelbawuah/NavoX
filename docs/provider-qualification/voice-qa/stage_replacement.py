"""Stage one pinned QA harness; default to a read-only live preflight.

Application images/configuration, existing claims/results and audio are never
changed. Paid execution additionally requires --mode launch and the explicit
six-call replacement approval flag. A launch attempt cannot automatically retry.
"""

from __future__ import annotations

import argparse
import ast
import datetime as dt
import fcntl
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.dont_write_bytecode = True

ROOT = Path("/srv/navox-vox-test")
DIRECTORY = ROOT / "browser/voice-qualification"
PROJECT = "navox-vox-test"
APP_SHA = "d21bf978df3c14d393fe05acf0fe41772841b23a"
RUN_ID = "voice-memory-20261027-b"
PROFILE = "post-startup-audit-three-calls"
STOPPED_RUN_ID = "voice-memory-20261027-a"
STOPPED_REPORT_SHA256 = (
    "2442c73461a9b6fa76ef05f3fc17ec55f60765a24572f66e6c1db9e06a3bd5ef"
)
MAX_FILE_BYTES = 256 * 1024
MAX_TOTAL_BYTES = 1024 * 1024
LIVE_CALLS_MAY_HAVE_STARTED = False
FILES = (
    "run_on_isolated.py",
    "qualify.cjs",
    "synthetic-mic.cjs",
    "fixtures.json",
    "prepare_audio.py",
    "check_limits_offline.py",
    "offline-probe.cjs",
    "README.md",
)
BASELINE_HASHES = {
    "run_on_isolated.py": "8192943ee88e918dcdb8a79fb2e566f6e9a830c94abf5f14a549f6779d11f6d5",
    "qualify.cjs": "7c625a55780cb271398b8fa2e642c13db617ff37566e19bfb642728cab5c90d8",
    "synthetic-mic.cjs": "1294647003d0832a16f1169ba915936ac96ff9d49fb9428430bd09693717065e",
    "fixtures.json": "cb4a58651d40d60b86c7551a07a4f84464f27580178907e45b6d67c6723c5e1c",
    "prepare_audio.py": "0cb57124de40bfc5b6b6860582cb90562e4c2231f3cded3afa654cbaa2a0f147",
    "check_limits_offline.py": "427f761489d1b3c9aa301d2b696734fb013dcb6eab6e5f1474a30f550d107316",
    "offline-probe.cjs": "cde150d842fb925b89b0a75a3ad24c3e9a444e7df864829fd5082fd747f4a1e7",
    "README.md": "2ba4713e49b058a506ee0751752459b925eac9cc08f6d89aa0d2df1e74682d47",
}

# Public, fixed validation names only. Arbitrary runtime exception text can
# contain private data and must never be forwarded to a diagnostic report.
VALIDATION_CHECKS = {
    "Full immutable QA commit required": "immutable-qa-commit",
    "Only the fixed QA file list may be fetched": "source-file-whitelist",
    "Pinned official raw GitHub URL required": "pinned-source-url",
    "Unexpected raw GitHub response": "pinned-source-response",
    "Oversized QA source": "source-content-length",
    "Empty or oversized QA source": "source-byte-bound",
    "Regular file required": "regular-file",
    "Bounded regular file required": "regular-file-byte-bound",
    "Exact bounded QA file set required": "exact-source-file-set",
    "QA source exceeded total byte limit": "source-total-byte-bound",
    "Existing fixture contract must stay unchanged": "fixture-contract",
    "One exact stopped execution report required": "single-predecessor-report",
    "Stopped execution report changed": "predecessor-report-fingerprint",
    "Replacement launch scope already consumed": "replacement-scope-unused",
    "Staged helper manifest or source changed": "staged-source-manifest",
    "Exact helper backup required": "helper-backup-directory",
    "Helper backup changed": "helper-backup-fingerprint",
    "Current helper source differs from reviewed stopped run": "installed-helper-fingerprints",
    "Fixed parent mode required": "parent-mode",
    "Parent preflight object required": "preflight-json-object",
    "Exact replacement limits required": "preflight-limit-object",
    "Preflight source or replacement limits differ": "preflight-source-and-limits",
    "Explicit six-call approval and successful preflight required": "approved-ready-launch",
    "Bounded own result log required": "result-log-byte-bound",
    "Ambiguous own result log": "single-parent-result",
    "Bounded result wait required": "result-wait-bound",
    "Launch requires a separate explicit scope approval": "launch-scope-approval",
    "Approval flag requires explicit launch mode": "explicit-launch-mode",
    "Exact regular isolated QA directory required": "isolated-qa-directory",
    "Exact isolated root required": "isolated-root",
    "Unexpected isolated project": "isolated-project",
    "Staged source changed after preflight": "post-preflight-source-fingerprint",
    "Result waiting requires explicitly approved launch": "approved-result-wait",
}


def digest(value):
    return hashlib.sha256(value).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True).encode()


def commit_sha(value):
    if not isinstance(value, str) or re.fullmatch(r"[a-f0-9]{40}", value) is None:
        raise ValueError("Full immutable QA commit required")
    return value


def raw_url(sha, filename):
    commit_sha(sha)
    if filename not in FILES:
        raise ValueError("Only the fixed QA file list may be fetched")
    return (
        "https://raw.githubusercontent.com/michaelbawuah/NavoX/"
        + sha
        + "/docs/provider-qualification/voice-qa/"
        + filename
    )


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def fetch_file(url):
    parsed = urllib.parse.urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.netloc != "raw.githubusercontent.com"
        or parsed.query
        or parsed.fragment
        or re.fullmatch(
            r"/michaelbawuah/NavoX/[a-f0-9]{40}/docs/provider-qualification/voice-qa/[^/]+",
            parsed.path,
        )
        is None
        or parsed.path.rsplit("/", 1)[1] not in FILES
    ):
        raise ValueError("Pinned official raw GitHub URL required")
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "NavoX-isolated-QA-stage",
            "Accept": "application/octet-stream",
        },
    )
    with urllib.request.build_opener(NoRedirect()).open(
        request, timeout=20
    ) as response:
        if response.status != 200 or response.geturl() != url:
            raise ValueError("Unexpected raw GitHub response")
        length = response.headers.get("Content-Length")
        if length is not None and (
            not length.isdecimal() or int(length) > MAX_FILE_BYTES
        ):
            raise ValueError("Oversized QA source")
        data = response.read(MAX_FILE_BYTES + 1)
    if not data or len(data) > MAX_FILE_BYTES:
        raise ValueError("Empty or oversized QA source")
    return data


def regular_bytes(path, limit):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError("Regular file required")
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError("Bounded regular file required")
    return data


def source_manifest(data):
    if set(data) != set(FILES) or any(
        not isinstance(value, bytes) or not value or len(value) > MAX_FILE_BYTES
        for value in data.values()
    ):
        raise ValueError("Exact bounded QA file set required")
    if sum(map(len, data.values())) > MAX_TOTAL_BYTES:
        raise ValueError("QA source exceeded total byte limit")
    if digest(data["fixtures.json"]) != BASELINE_HASHES["fixtures.json"]:
        raise ValueError("Existing fixture contract must stay unchanged")
    json.loads(data["fixtures.json"])
    for filename in FILES:
        if filename.endswith(".py"):
            ast.parse(data[filename], filename=filename)
    return {name: digest(data[name]) for name in FILES}


def stopped_report(directory):
    path = directory / ("execution-" + STOPPED_RUN_ID + ".log")
    raw = regular_bytes(path, 2 * 1024 * 1024).decode()
    objects = []
    for line in raw.splitlines():
        try:
            value = json.loads(line)
        except ValueError:
            continue
        if isinstance(value, dict):
            objects.append(value)
    if len(objects) != 1:
        raise ValueError("One exact stopped execution report required")
    value = objects[0]
    if (
        digest(canonical(value)) != STOPPED_REPORT_SHA256
        or value.get("sha") != APP_SHA
        or value.get("qualification_run_id") != STOPPED_RUN_ID
        or value.get("passed") is not False
        or value.get("source_preflight", {}).get("helper_sha256") != BASELINE_HASHES
    ):
        raise ValueError("Stopped execution report changed")
    return value


def guard_not_started(directory):
    fixed = [
        directory / ("run-" + RUN_ID + ".claim.json"),
        directory / ("execution-" + RUN_ID + ".log"),
        directory / ("launch-" + RUN_ID + ".json"),
    ]
    if any(path.exists() or path.is_symlink() for path in fixed) or any(
        directory.glob("scope-" + PROFILE + "-*.claim.json")
    ):
        raise ValueError("Replacement launch scope already consumed")


def atomic_write(path, data):
    fd, temporary = tempfile.mkstemp(prefix=".replacement-stage-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def exclusive_write(path, data):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def stage_files(directory, sha, data):
    """Replace exactly eight verified helpers, retaining their exact old bytes."""
    commit_sha(sha)
    guard_not_started(directory)
    stopped_report(directory)
    hashes = source_manifest(data)
    current = {name: regular_bytes(directory / name, MAX_FILE_BYTES) for name in FILES}
    current_hashes = {name: digest(value) for name, value in current.items()}
    manifest_path = directory / ("stage-" + RUN_ID + ".json")
    backup = directory / ("helper-backup-" + RUN_ID)
    manifest = {
        "schema": "navox-pinned-replacement-stage.v1",
        "qa_commit_sha": sha,
        "app_sha": APP_SHA,
        "run_id": RUN_ID,
        "profile": PROFILE,
        "baseline_helper_sha256": BASELINE_HASHES,
        "helper_sha256": hashes,
    }
    if manifest_path.exists() or manifest_path.is_symlink():
        existing = json.loads(regular_bytes(manifest_path, MAX_FILE_BYTES))
        if existing != manifest or current_hashes != hashes:
            raise ValueError("Staged helper manifest or source changed")
        if backup.is_symlink() or not backup.is_dir():
            raise ValueError("Exact helper backup required")
        if {
            name: digest(regular_bytes(backup / name, MAX_FILE_BYTES)) for name in FILES
        } != BASELINE_HASHES:
            raise ValueError("Helper backup changed")
        return {"idempotent": True, **manifest}
    if current_hashes != BASELINE_HASHES:
        raise ValueError("Current helper source differs from reviewed stopped run")
    guard_not_started(directory)
    backup.mkdir(mode=0o700)  # Exclusive; an incomplete prior attempt requires review.
    for name in FILES:
        exclusive_write(backup / name, current[name])
    exclusive_write(
        backup / "baseline.json",
        canonical({"app_sha": APP_SHA, "helper_sha256": BASELINE_HASHES}),
    )
    replaced = []
    try:
        for name in FILES:
            atomic_write(directory / name, data[name])
            replaced.append(name)
        atomic_write(manifest_path, canonical(manifest))
    except Exception:
        # Roll back only this attempt's bytes, never an independently changed file.
        for name in replaced:
            if digest(regular_bytes(directory / name, MAX_FILE_BYTES)) == hashes[name]:
                atomic_write(directory / name, current[name])
        raise
    return {"idempotent": False, **manifest}


def parent_command(directory, mode):
    if mode not in ("preflight", "authorized-run"):
        raise ValueError("Fixed parent mode required")
    command = [
        sys.executable,
        "-B",
        str(directory / "run_on_isolated.py"),
        mode,
        APP_SHA,
        "--profile",
        PROFILE,
        "--prior-provider-usd",
        "0.0745244",
        "--prior-connected-ms",
        "65521",
        "--prior-call-count",
        "3",
        "--prior-uncertain-streams",
        "1",
        "--prior-reserved-tokens",
        "10525",
        "--qualification-run-id",
        RUN_ID,
    ]
    if mode == "authorized-run":
        command += [
            "--root-explicitly-authorized-live-provider-calls",
            "--root-explicitly-authorized-six-call-replacement-scope",
        ]
    return command


def run_preflight(directory, manifest, run=subprocess.run):
    child = run(
        parent_command(directory, "preflight"),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=120,
    )
    if child.returncode != 0 or len(child.stdout.encode()) > 2 * 1024 * 1024:
        raise RuntimeError("Parent preflight did not complete")
    value = json.loads(child.stdout)
    if not isinstance(value, dict):
        raise ValueError("Parent preflight object required")  # noqa: TRY004 - invalid serialized value
    limits = value.get("qualification_limits", {})
    if not isinstance(limits, dict):
        raise ValueError("Exact replacement limits required")  # noqa: TRY004 - invalid serialized value
    exact = {
        "profile": PROFILE,
        "qualification_run_id": RUN_ID,
        "max_calls": 3,
        "prior_call_count": 3,
        "total_call_ceiling": 6,
        "prior_connected_ms": 65521,
        "prior_provider_usd": "0.0745244",
        "remaining_provider_usd": "1.9254756",
        "remaining_transport_ms": 117564,
        "total_transport_ceiling_ms": 183085,
        "prior_uncertain_streams": 1,
        "prior_reserved_tokens": 10525,
        "financial_qualified": False,
    }
    valid = (
        value.get("sha") == APP_SHA
        and value.get("app_source_sha") == APP_SHA
        and value.get("scope") == "isolated-test"
        and value.get("readiness_only") is True
        and value.get("provider_calls") == 0
        and value.get("helper_sha256") == manifest["helper_sha256"]
        and all(limits.get(key) == item for key, item in exact.items())
    )
    if not valid:
        raise ValueError("Preflight source or replacement limits differ")
    prior = value.get("prior_usage", {})
    ledger = prior.get("ledger_sha256") if isinstance(prior, dict) else None
    ledger_valid = (
        isinstance(ledger, str) and re.fullmatch(r"[a-f0-9]{64}", ledger) is not None
    )
    ready = (
        value.get("ready") is True
        and isinstance(prior, dict)
        and prior.get("verified") is True
        and ledger_valid
        and value.get("run_id_unused") is True
        and value.get("scope_unused") is True
    )
    return {
        "ready": ready,
        "exact_pinned_harness": True,
        "prior_usage_verified": isinstance(prior, dict)
        and prior.get("verified") is True,
        "prior_ledger_sha256": ledger if ledger_valid else None,
        "run_id_unused": value.get("run_id_unused") is True,
        "scope_unused": value.get("scope_unused") is True,
        "app_sha": APP_SHA,
        "profile": PROFILE,
        "run_id": RUN_ID,
        "new_call_limit": 3,
        "cumulative_call_limit": 6,
        "prior_known_provider_estimate_usd": "0.0745244",
        "remaining_known_provider_estimate_usd": "1.9254756",
        "cumulative_known_provider_estimate_stop_usd": "2",
        "remaining_transport_ms": 117564,
        "cumulative_transport_ceiling_ms": 183085,
        "financial_qualified": False,
        "provider_calls_started_by_preflight": 0,
    }


def launch(directory, sha, approved, preflight, popen=subprocess.Popen):
    global LIVE_CALLS_MAY_HAVE_STARTED
    if approved is not True or preflight.get("ready") is not True:
        raise ValueError("Explicit six-call approval and successful preflight required")
    guard_not_started(directory)
    path = directory / ("execution-" + RUN_ID + ".log")
    record_path = directory / ("launch-" + RUN_ID + ".json")
    record = {
        "run_id": RUN_ID,
        "app_sha": APP_SHA,
        "qa_commit_sha": commit_sha(sha),
        "explicit_six_call_scope_approved": True,
        "state": "launch-reserved",
        "execution_log": str(path),
        "financial_qualified": False,
    }
    exclusive_write(record_path, canonical(record))
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as output:
        LIVE_CALLS_MAY_HAVE_STARTED = True
        child = popen(
            parent_command(directory, "authorized-run"),
            stdin=subprocess.DEVNULL,
            stdout=output,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            close_fds=True,
        )
    record.update(
        {
            "state": "launched-awaiting-result",
            "pid": child.pid,
            "launched_at": dt.datetime.now(dt.UTC).isoformat(),
        }
    )
    atomic_write(record_path, canonical(record))
    return record


def completed_result(raw):
    """Accept only this fixed parent's result; never forward arbitrary log lines."""
    if not isinstance(raw, bytes) or len(raw) > 2 * 1024 * 1024:
        raise ValueError("Bounded own result log required")
    found = []
    for line in raw.decode().splitlines():
        try:
            value = json.loads(line)
        except ValueError:
            continue
        if not isinstance(value, dict):
            continue
        limits = value.get("qualification_limits", {})
        if (
            value.get("sha") == APP_SHA
            and value.get("scope") == "isolated-test"
            and isinstance(limits, dict)
            and limits.get("profile") == PROFILE
            and limits.get("qualification_run_id") == RUN_ID
            and (
                (
                    value.get("qualification_run_id") == RUN_ID
                    and type(value.get("passed")) is bool
                )
                or value.get("execution_refused") is True
            )
        ) or (
            set(value) == {"ok", "failure_class", "provider_calls_may_have_started"}
            and value.get("ok") is False
            and type(value.get("provider_calls_may_have_started")) is bool
            and isinstance(value.get("failure_class"), str)
            and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", value["failure_class"])
        ):
            found.append(value)
    if len(found) > 1:
        raise ValueError("Ambiguous own result log")
    return found[0] if found else None


def wait_result(
    directory,
    *,
    clock=time.monotonic,
    pause=time.sleep,
    read=regular_bytes,
    timeout=420,
):
    if not isinstance(timeout, (int, float)) or not 0 < timeout <= 420:
        raise ValueError("Bounded result wait required")
    path = directory / ("execution-" + RUN_ID + ".log")
    deadline = clock() + timeout
    while True:
        result = completed_result(read(path, 2 * 1024 * 1024))
        if result is not None:
            return result
        remaining = deadline - clock()
        if remaining <= 0:
            return {
                "schema": "navox-replacement-result-pending.v1",
                "run_id": RUN_ID,
                "result_pending": True,
                "execution_log": str(path),
                "retry_authorized": False,
                "qualification_passed": False,
            }
        pause(min(2, remaining))


def execute(
    sha,
    *,
    mode="preflight",
    approved=False,
    directory=DIRECTORY,
    ops=None,
    fetch=fetch_file,
    run=subprocess.run,
    popen=subprocess.Popen,
):
    commit_sha(sha)
    if mode not in ("preflight", "launch") or (
        mode == "launch" and approved is not True
    ):
        raise ValueError("Launch requires a separate explicit scope approval")
    if approved and mode != "launch":
        raise ValueError("Approval flag requires explicit launch mode")
    if (
        directory.is_symlink()
        or not directory.is_dir()
        or directory.resolve() != directory
    ):
        raise ValueError("Exact regular isolated QA directory required")
    guard_not_started(directory)
    if ops is None:
        if ROOT.resolve() != ROOT or directory != DIRECTORY:
            raise ValueError("Exact isolated root required")
        sys.path.insert(0, str(ROOT / "ops"))
        import prepare_release as ops
    ops.guard()
    if ops.PROJECT != PROJECT:
        raise ValueError("Unexpected isolated project")
    # A fixed local lock serializes staging/launch, never a database reservation.
    fd = os.open(
        directory / ("stage-" + RUN_ID + ".lock"),
        os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW,
        0o600,
    )
    with os.fdopen(fd, "r+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        guard_not_started(directory)
        stopped_report(directory)
        data = {name: fetch(raw_url(sha, name)) for name in FILES}
        staged = stage_files(directory, sha, data)
        preflight = run_preflight(directory, staged, run)
        result = {
            "schema": "navox-pinned-replacement-stage-result.v1",
            "mode": mode,
            "qa_commit_sha": sha,
            "app_sha": APP_SHA,
            "run_id": RUN_ID,
            "staged_helper_sha256": staged["helper_sha256"],
            "idempotent_stage": staged["idempotent"],
            "preflight": preflight,
            "ready": preflight["ready"],
            "application_or_configuration_changed": False,
            "live_execution_launched": False,
            "financial_qualified": False,
        }
        if mode == "launch":
            if preflight["ready"] is not True:
                result["execution_refused"] = True
            else:
                if {
                    name: digest(regular_bytes(directory / name, MAX_FILE_BYTES))
                    for name in FILES
                } != staged["helper_sha256"]:
                    raise ValueError("Staged source changed after preflight")
                result["launch"] = launch(directory, sha, approved, preflight, popen)
                result["live_execution_launched"] = True
        return result


def arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("qa_commit_sha", type=commit_sha)
    parser.add_argument("--mode", choices=("preflight", "launch"), default="preflight")
    parser.add_argument("--approve-six-call-replacement", action="store_true")
    parser.add_argument(
        "--wait-result",
        action="store_true",
        help="After detached launch, poll only its result log for at most 420 seconds.",
    )
    return parser.parse_args(argv)


def main(argv=None):
    os.umask(0o077)
    try:
        selected = arguments(argv)
        if selected.wait_result and (
            selected.mode != "launch" or not selected.approve_six_call_replacement
        ):
            raise ValueError("Result waiting requires explicitly approved launch")
        result = execute(
            selected.qa_commit_sha,
            mode=selected.mode,
            approved=selected.approve_six_call_replacement,
        )
        print(json.dumps(result), flush=True)
        if selected.wait_result and result.get("live_execution_launched") is True:
            completed = wait_result(DIRECTORY)
            print(json.dumps(completed), flush=True)
            return 0 if completed.get("passed") is True else 1
        return 0 if result["ready"] else 1
    except Exception as error:  # noqa: BLE001 - emit only sanitized failure metadata
        # Never forward network/child stderr, response bodies, URLs or exception text.
        print(
            json.dumps(
                {
                    "ok": False,
                    "failure_class": type(error).__name__,
                    "validation_check": VALIDATION_CHECKS.get(
                        str(error), "unclassified"
                    )
                    if isinstance(error, ValueError)
                    else None,
                    "run_id": RUN_ID,
                    "retry_authorized": False,
                    "provider_calls_may_have_started": LIVE_CALLS_MAY_HAVE_STARTED,
                }
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
