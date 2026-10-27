"""Offline staging/approval/result-wait regressions; no network or provider I/O."""

from __future__ import annotations

import contextlib
import copy
import io
import json
import subprocess
import tempfile
from pathlib import Path
from types import SimpleNamespace

import stage_replacement as helper

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
BASE = "b51e82a9b175ec3f1a26fee12795753ce2f6ca47"
QA_SHA = "1" * 40
old = {
    name: subprocess.check_output(
        ["git", "show", BASE + ":docs/provider-qualification/voice-qa/" + name],
        cwd=REPO,
    )
    for name in helper.FILES
}
new = {name: (HERE / name).read_bytes() for name in helper.FILES}
assert {
    name: helper.digest(data) for name, data in old.items()
} == helper.BASELINE_HASHES
new_hashes = helper.source_manifest(new)
report = json.loads((HERE / "results/voice-memory-20261027-a.json").read_text())[
    "supplied_report"
]
assert helper.digest(helper.canonical(report)) == helper.STOPPED_REPORT_SHA256


class FakeOps:
    PROJECT = helper.PROJECT

    def __init__(self):
        self.guards = 0

    def guard(self):
        self.guards += 1


def initial(directory):
    directory.mkdir()
    for name, data in old.items():
        (directory / name).write_bytes(data)
    (directory / ("execution-" + helper.STOPPED_RUN_ID + ".log")).write_text(
        json.dumps(report) + "\n"
    )
    protected = {
        "run-voice-memory-20261027-a.claim.json": b"old-run",
        "scope-fresh-three-calls-old.claim.json": b"old-scope",
        "result-d21-old.json": b"old-result",
        "fixtures/source.wav": b"old-audio",
    }
    (directory / "fixtures").mkdir()
    for name, data in protected.items():
        (directory / name).write_bytes(data)
    return protected


def fetch(url):
    assert url.startswith(
        "https://raw.githubusercontent.com/michaelbawuah/NavoX/"
        + QA_SHA
        + "/docs/provider-qualification/voice-qa/"
    )
    return new[url.rsplit("/", 1)[1]]


def preflight_result(ready=True):
    return {
        "sha": helper.APP_SHA,
        "app_source_sha": helper.APP_SHA,
        "scope": "isolated-test",
        "readiness_only": True,
        "provider_calls": 0,
        "helper_sha256": new_hashes,
        "ready": ready,
        "prior_usage": {"verified": True, "ledger_sha256": "2" * 64},
        "run_id_unused": True,
        "scope_unused": True,
        "qualification_limits": {
            "profile": helper.PROFILE,
            "qualification_run_id": helper.RUN_ID,
            "max_calls": 3,
            "prior_call_count": 3,
            "total_call_ceiling": 6,
            "prior_provider_usd": "0.0745244",
            "remaining_provider_usd": "1.9254756",
            "prior_connected_ms": 65521,
            "remaining_transport_ms": 117564,
            "total_transport_ceiling_ms": 183085,
            "prior_uncertain_streams": 1,
            "prior_reserved_tokens": 10525,
            "financial_qualified": False,
        },
    }


def run_preflight(command, **kwargs):
    assert command[3:5] == ["preflight", helper.APP_SHA]
    assert "--root-explicitly-authorized-live-provider-calls" not in command
    assert "--root-explicitly-authorized-six-call-replacement-scope" not in command
    assert kwargs == {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": True,
        "timeout": 120,
    }
    return SimpleNamespace(
        returncode=0,
        stdout=json.dumps(preflight_result()),
        stderr="private child text must never appear",
    )


def never(*args, **kwargs):
    raise AssertionError("Unexpected I/O or launch")


def rejected(action):
    try:
        action()
    except (ValueError, FileExistsError):
        return
    raise AssertionError("Unsafe staging/launch accepted")


with tempfile.TemporaryDirectory() as temporary:
    root = Path(temporary)
    directory = root / "qa"
    protected = initial(directory)
    ops = FakeOps()
    first = helper.execute(
        QA_SHA,
        directory=directory,
        ops=ops,
        fetch=fetch,
        run=run_preflight,
        popen=never,
    )
    assert first["ready"] is True and first["mode"] == "preflight"
    assert (
        first["live_execution_launched"] is False and first["idempotent_stage"] is False
    )
    backup = directory / ("helper-backup-" + helper.RUN_ID)
    for name in helper.FILES:
        assert (directory / name).read_bytes() == new[name]
        assert (backup / name).read_bytes() == old[name]
        assert (directory / name).stat().st_mode & 0o777 == 0o600
        assert (backup / name).stat().st_mode & 0o777 == 0o600
    for name, data in protected.items():
        assert (directory / name).read_bytes() == data
    before = {name: (directory / name).stat().st_mtime_ns for name in helper.FILES}
    second = helper.execute(
        QA_SHA,
        directory=directory,
        ops=ops,
        fetch=fetch,
        run=run_preflight,
        popen=never,
    )
    assert second["idempotent_stage"] is True and ops.guards == 2
    assert before == {
        name: (directory / name).stat().st_mtime_ns for name in helper.FILES
    }
    assert first["preflight"]["cumulative_call_limit"] == 6
    assert first["preflight"]["remaining_transport_ms"] == 117564
    assert "private child text" not in json.dumps(first)

    launches = []

    def fake_popen(command, **kwargs):
        launches.append(command)
        assert command[3:5] == ["authorized-run", helper.APP_SHA]
        assert "--root-explicitly-authorized-live-provider-calls" in command
        assert "--root-explicitly-authorized-six-call-replacement-scope" in command
        assert (
            kwargs["stdin"] == subprocess.DEVNULL
            and kwargs["stderr"] == subprocess.STDOUT
        )
        assert kwargs["start_new_session"] is True and kwargs["close_fds"] is True
        assert not kwargs["stdout"].closed
        return SimpleNamespace(pid=12345)

    launched = helper.execute(
        QA_SHA,
        mode="launch",
        approved=True,
        directory=directory,
        ops=ops,
        fetch=fetch,
        run=run_preflight,
        popen=fake_popen,
    )
    assert launched["live_execution_launched"] is True and len(launches) == 1
    assert launched["launch"]["pid"] == 12345
    rejected(
        lambda: helper.execute(
            QA_SHA,
            mode="launch",
            approved=True,
            directory=directory,
            ops=ops,
            fetch=never,
            run=never,
            popen=never,
        )
    )
    rejected(
        lambda: helper.execute(
            QA_SHA, directory=directory, ops=ops, fetch=never, run=never, popen=never
        )
    )
    for name, data in protected.items():
        assert (directory / name).read_bytes() == data

    for mode, approval in (("launch", False), ("preflight", True), ("other", True)):
        fresh = root / ("invalid-" + mode + str(approval))
        initial(fresh)
        rejected(
            lambda mode=mode, approval=approval, fresh=fresh: helper.execute(
                QA_SHA,
                mode=mode,
                approved=approval,
                directory=fresh,
                ops=FakeOps(),
                fetch=never,
                run=never,
                popen=never,
            )
        )
        assert not (fresh / ("stage-" + helper.RUN_ID + ".lock")).exists()

    for marker in (
        "run-" + helper.RUN_ID + ".claim.json",
        "scope-" + helper.PROFILE + "-old.claim.json",
    ):
        fresh = root / ("claimed-" + marker)
        initial(fresh)
        (fresh / marker).write_bytes(b"must remain")
        rejected(
            lambda fresh=fresh: helper.execute(
                QA_SHA,
                directory=fresh,
                ops=FakeOps(),
                fetch=never,
                run=never,
                popen=never,
            )
        )
        assert (fresh / marker).read_bytes() == b"must remain"

    fresh = root / "tampered"
    initial(fresh)
    (fresh / "qualify.cjs").write_bytes(b"unexpected source")
    rejected(
        lambda: helper.execute(
            QA_SHA, directory=fresh, ops=FakeOps(), fetch=fetch, run=never, popen=never
        )
    )
    assert (fresh / "qualify.cjs").read_bytes() == b"unexpected source"
    assert not (fresh / ("helper-backup-" + helper.RUN_ID)).exists()

    fresh = root / "wrong-report"
    initial(fresh)
    changed = copy.deepcopy(report)
    changed["passed"] = True
    (fresh / ("execution-" + helper.STOPPED_RUN_ID + ".log")).write_text(
        json.dumps(changed)
    )
    rejected(
        lambda: helper.execute(
            QA_SHA, directory=fresh, ops=FakeOps(), fetch=never, run=never, popen=never
        )
    )
    assert not (fresh / ("helper-backup-" + helper.RUN_ID)).exists()

    fresh = root / "not-ready"
    initial(fresh)

    def not_ready(command, **kwargs):
        result = run_preflight(command, **kwargs)
        result.stdout = json.dumps(preflight_result(False))
        return result

    refused = helper.execute(
        QA_SHA,
        mode="launch",
        approved=True,
        directory=fresh,
        ops=FakeOps(),
        fetch=fetch,
        run=not_ready,
        popen=never,
    )
    assert (
        refused["execution_refused"] is True
        and refused["live_execution_launched"] is False
    )
    assert not (fresh / ("execution-" + helper.RUN_ID + ".log")).exists()

    # Bounded result polling forwards the exact known parent JSON, not stderr.
    parent = {
        "sha": helper.APP_SHA,
        "scope": "isolated-test",
        "qualification_run_id": helper.RUN_ID,
        "qualification_limits": {
            "profile": helper.PROFILE,
            "qualification_run_id": helper.RUN_ID,
        },
        "passed": False,
    }
    now = [0]
    sleeps = []
    reads = [
        b"private stderr\n",
        b'{"private":"never forward"}\n',
        json.dumps(parent).encode(),
    ]

    def clock():
        return now[0]

    def pause(seconds):
        assert 0 < seconds <= 2
        sleeps.append(seconds)
        now[0] += seconds

    def read(path, limit):
        assert (
            path.name == "execution-" + helper.RUN_ID + ".log"
            and limit == 2 * 1024 * 1024
        )
        return reads.pop(0)

    assert (
        helper.wait_result(directory, clock=clock, pause=pause, read=read, timeout=8)
        == parent
    )
    assert sleeps == [2, 2]
    now[0] = 0
    pending = helper.wait_result(
        directory, clock=clock, pause=pause, read=lambda path, limit: b"", timeout=5
    )
    assert (
        now[0] == 5
        and pending["result_pending"] is True
        and pending["retry_authorized"] is False
    )
    rejected(
        lambda: helper.wait_result(
            directory, clock=clock, pause=never, read=never, timeout=421
        )
    )
    rejected(lambda: helper.completed_result(b"x" * (2 * 1024 * 1024 + 1)))
    wrong = copy.deepcopy(parent)
    wrong["qualification_run_id"] = "another-run"
    assert helper.completed_result(json.dumps(wrong).encode()) is None
    outer = {
        "ok": False,
        "failure_class": "TimeoutExpired",
        "provider_calls_may_have_started": True,
    }
    assert helper.completed_result(json.dumps(outer).encode()) == outer
    assert (
        helper.completed_result(b'{"ok":false,"failure_class":"private response body"}')
        is None
    )

for value in ("main", "../unsafe", "A" * 40, "1" * 39, "https://example.com"):
    rejected(lambda value=value: helper.commit_sha(value))
for filename in (
    "../runtime.env",
    "inspect_completed_run.py",
    "fixtures/source.wav",
    "",
):
    rejected(lambda filename=filename: helper.raw_url(QA_SHA, filename))
for url in (
    "http://raw.githubusercontent.com/x",
    "https://example.com/x",
    helper.raw_url(QA_SHA, "qualify.cjs") + "?unsafe=1",
    "https://raw.githubusercontent.com/michaelbawuah/NavoX/main/docs/provider-qualification/voice-qa/qualify.cjs",
    "https://raw.githubusercontent.com/michaelbawuah/NavoX/"
    + QA_SHA
    + "/docs/provider-qualification/voice-qa/runtime.env",
):
    rejected(
        lambda url=url: helper.fetch_file(url)
    )  # Rejected before any opener/network call.
assert (
    helper.NoRedirect().redirect_request(
        None, None, 302, "redirect", {}, "https://example.com"
    )
    is None
)
changed = dict(new)
changed["fixtures.json"] = b"{}"
rejected(lambda: helper.source_manifest(changed))
changed = dict(new)
changed["README.md"] = b"x" * (helper.MAX_FILE_BYTES + 1)
rejected(lambda: helper.source_manifest(changed))
selected = helper.arguments([QA_SHA])
assert (
    selected.mode == "preflight"
    and not selected.approve_six_call_replacement
    and not selected.wait_result
)

# A failure after launch never claims that no provider call could have started.
# The launch receipt must already be flushed before result polling begins.
actual_execute, actual_wait = helper.execute, helper.wait_result
try:
    helper.execute = lambda *args, **kwargs: {
        "ready": True,
        "live_execution_launched": True,
        "launch": {"pid": 12345},
    }
    output = io.StringIO()

    def broken_wait(directory):
        assert json.loads(output.getvalue())["launch"]["pid"] == 12345
        raise OSError("private diagnostic must never appear")

    helper.wait_result = broken_wait
    with contextlib.redirect_stdout(output):
        status = helper.main(
            [
                QA_SHA,
                "--mode",
                "launch",
                "--approve-six-call-replacement",
                "--wait-result",
            ]
        )
    lines = [json.loads(value) for value in output.getvalue().splitlines()]
    assert status == 1 and len(lines) == 2
    assert (
        lines[1]["provider_calls_may_have_started"] is True
        and lines[1]["retry_authorized"] is False
    )
    assert "private diagnostic" not in output.getvalue()
    helper.wait_result = lambda directory: parent
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        status = helper.main(
            [
                QA_SHA,
                "--mode",
                "launch",
                "--approve-six-call-replacement",
                "--wait-result",
            ]
        )
    assert status == 1 and json.loads(output.getvalue().splitlines()[-1]) == parent
finally:
    helper.execute, helper.wait_result = actual_execute, actual_wait
print(
    json.dumps(
        {
            "offline_replacement_stage": "pass",
            "exact_stopped_source_and_pinned_fetch": "pass",
            "backup_idempotency_and_claim_preservation": "pass",
            "explicit_detached_launch_only": "pass",
            "bounded_content_free_result_wait": "pass",
            "provider_calls": 0,
            "external_requests": 0,
        }
    )
)
