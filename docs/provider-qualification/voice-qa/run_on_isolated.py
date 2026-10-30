"""Readiness by default; live calls require root's explicit invocation after deployment.

This helper runs only on the existing isolated server. Credentials/cookies stay in
memory and an anonymous child stdin pipe, never in files, argv or diagnostics.
"""

from __future__ import annotations

import argparse
import datetime as dt
from decimal import Decimal
import hashlib
import http.cookiejar
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request


ROOT = Path("/srv/navox-vox-test")
SITE = "https://vox-test.66-175-235-169.nip.io"
PROJECT = "navox-vox-test"
DIRECTORY = ROOT / "browser/voice-qualification"
IMAGE = "mcr.microsoft.com/playwright:v1.64.1-noble"
LIVE_CALLS_MAY_HAVE_STARTED = False
os.umask(0o077)
sys.path.insert(0, str(ROOT / "ops"))


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def api(opener, resource, *, method="GET", body=None, account=None, loopback=False, private_loopback_cookie=None):
    base = "http://127.0.0.1:18000" if loopback else SITE
    headers = {"Origin": SITE, "Cache-Control": "no-store"}
    if private_loopback_cookie is not None:
        if not loopback or resource not in ("/auth/logout", "/auth/me"):
            raise ValueError("Private registration cookie is loopback-revocation-only")
        headers["Cookie"] = private_loopback_cookie
    if account:
        headers["X-NavoX-Account"] = account
    if body is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        base + "/api/v1" + resource,
        headers=headers,
        data=json.dumps(body).encode() if body is not None else None,
        method=method,
    )
    try:
        with opener.open(request, timeout=15) as response:
            raw = response.read(2 * 1024 * 1024 + 1)
            if len(raw) > 2 * 1024 * 1024:
                raise RuntimeError("Oversized qualification response")
            return response.status, json.loads(raw) if raw else None
    except urllib.error.HTTPError as error:
        # Provider/session error bodies are private and intentionally not read.
        return error.code, None


def required(opener, resource, **kwargs):
    status, value = api(opener, resource, **kwargs)
    if status not in (200, 201, 204):
        raise RuntimeError("Qualification HTTP request failed")
    return value


def nonnegative(value):
    try:
        parsed = Decimal(str(value))
    except Exception:
        return False
    return value is not None and parsed.is_finite() and parsed >= 0


def qualification_limits(profile, prior_provider_usd=None, prior_connected_ms=None, prior_call_count=None):
    """Root supplies preserved, settled usage; this never resets an allowance."""
    limits = {"profile": profile, "max_calls": 3, "prior_call_count": 0,
              "total_call_ceiling": 3, "prior_provider_usd": "0", "prior_connected_ms": 0,
              "remaining_provider_usd": "2", "remaining_transport_ms": 135000}
    if profile == "full-three-calls":
        if any(value is not None for value in (prior_provider_usd, prior_connected_ms, prior_call_count)):
            raise ValueError("Prior usage requires the explicit continuation profile")
        return limits
    if profile != "neutral-private-only" or prior_call_count != 1:
        raise ValueError("Continuation requires exactly one root-verified prior call")
    if not nonnegative(prior_provider_usd) or Decimal(str(prior_provider_usd)) >= 2:
        raise ValueError("Continuation provider estimate is unknown or exhausted")
    if type(prior_connected_ms) is not int or not 0 < prior_connected_ms <= 45000:
        raise ValueError("Continuation needs verified time and two remaining call windows")
    cost = Decimal(str(prior_provider_usd))
    return {**limits, "max_calls": 2, "prior_call_count": 1,
            "prior_provider_usd": str(cost), "prior_connected_ms": prior_connected_ms,
            "remaining_provider_usd": str(Decimal(2) - cost),
            "remaining_transport_ms": 135000 - prior_connected_ms}


def native_binding(ops, sha):
    """Inspect exact running source without importing providers or opening a session."""
    code = r'''
import ast, hashlib, json
from pathlib import Path
paths = {
    "worker": Path("/app/navox/vox/worker_livekit.py"),
    "tool": Path("/app/navox/vox/memory_learning_tools.py"),
    "status": Path("/app/navox/memory/learning_runtime.py"),
}
sources = {name: path.read_bytes() if path.is_file() else None for name, path in paths.items()}
trees = {name: ast.parse(value) if value is not None else None for name, value in sources.items()}
worker, tool, status = trees["worker"], trees["tool"], trees["status"]
binding = []
for node in ast.walk(worker) if worker is not None else ():
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "VoxReadTools":
        binding.extend(item for item in node.args if isinstance(item, ast.Call) and isinstance(item.func, ast.Name) and item.func.id == "MemoryLearningTools")
current_call = any(any(keyword.arg == "call_id" and isinstance(keyword.value, ast.Attribute) and keyword.value.attr == "call_id" and isinstance(keyword.value.value, ast.Name) and keyword.value.value.id == "context_key" for keyword in item.keywords) for item in binding)
tool_name = any(isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == "LEARN_MEMORY_TOOL" for target in node.targets) and isinstance(node.value, ast.Constant) and node.value.value == "learn_completed_memory" for node in ast.walk(tool)) if tool is not None else False
tool_class = next((node for node in ast.walk(tool) if isinstance(node, ast.ClassDef) and node.name == "MemoryLearningTools"), None) if tool is not None else None
native_enqueue = any(isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "enqueue_learning" and any(keyword.arg == "native" and isinstance(keyword.value, ast.Constant) and keyword.value.value is True for keyword in node.keywords) for node in ast.walk(tool_class)) if tool_class is not None else False
status_function = next((node for node in ast.walk(status) if isinstance(node, ast.AsyncFunctionDef) and node.name == "native_configured"), None) if status is not None else None
independent = status_function is not None and not any(isinstance(node, ast.Attribute) and node.attr == "memory_semantic_learning_enabled" for node in ast.walk(status_function))
definition = next((node for node in tool_class.body if isinstance(node, ast.FunctionDef) and node.name == "definitions"), None) if tool_class is not None else None
ungated_definition = definition is not None and not any(isinstance(node, ast.Attribute) and node.attr == "memory_semantic_learning_enabled" for node in ast.walk(definition))
print(json.dumps({"module_present": tool is not None, "worker_group_binding": bool(binding), "current_call_bound": current_call, "tool_name_matches": tool_name, "native_enqueue": native_enqueue, "background_flag_independent": independent and ungated_definition, "source_hashes": {name: hashlib.sha256(value).hexdigest() if value is not None else None for name, value in sources.items()}}))
'''
    observed = json.loads(ops.run(["docker", "exec", PROJECT + "-vox-worker-1", "/app/.venv/bin/python", "-B", "-c", code], text=True))
    release = ROOT / "releases" / sha[:12] / "services/api/navox"
    expected_files = {"worker": release / "vox/worker_livekit.py", "tool": release / "vox/memory_learning_tools.py", "status": release / "memory/learning_runtime.py"}
    expected = {name: hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None for name, path in expected_files.items()}
    observed["matches_exact_release_source"] = observed["source_hashes"] == expected
    observed["ready"] = all(observed.get(name) is True for name in ("module_present", "worker_group_binding", "current_call_bound", "tool_name_matches", "native_enqueue", "background_flag_independent", "matches_exact_release_source"))
    return observed


def preflight(sha: str, stt_shape: str):
    import prepare_release as ops

    ops.guard()
    if ROOT.resolve() != ROOT or ops.PROJECT != PROJECT:
        raise RuntimeError("Unexpected isolated root")
    for role in ("api", "worker", "vox-worker", "vox-cleanup", "web"):
        container = PROJECT + "-" + role + "-1"
        state = json.loads(ops.inspect(container, ".State"))
        image_id = json.loads(ops.inspect(container, ".Image"))
        info = json.loads(ops.run(["docker", "image", "inspect", image_id], text=True))[0]
        if not state.get("Running") or state.get("Restarting") or (info.get("Config", {}).get("Labels") or {}).get("org.opencontainers.image.revision") != sha:
            raise RuntimeError("Isolated running source is not exact and stable")
    environment = dict(item.split("=", 1) for item in json.loads(ops.inspect(ops.API, ".Config.Env")) if "=" in item)
    config = json.loads((ROOT / "inference.json").read_text())
    policy_row = json.loads(ops.psql("SELECT json_build_object('revision',revision,'definition',definition) FROM vox_usage_policy WHERE id=1"))
    policy = policy_row["definition"]
    prices = {(row["provider"], row["model"]): row for row in policy.get("model_pricing", [])}
    fields = {
        "realtime": ("input_text", "cached_text", "output_text", "input_audio", "cached_audio", "output_audio"),
        "llm": ("input_text", "cached_text", "output_text"),
        "tts": ("input_text", "output_audio"),
        "stt": ("input_text", "cached_text", "input_audio", "cached_audio", "output_text") if stt_shape == "tokens" else ("audio_usd_per_minute",),
        "web_search": ("input_text", "cached_text", "output_text"),
    }
    route_readiness = []
    for name, needed in fields.items():
        route = config.get(name)
        if route is None and name == "web_search":
            continue
        if not isinstance(route, dict):
            raise RuntimeError("Required configured route is absent")
        price = prices.get((route.get("provider"), route.get("model")))
        missing = [field for field in needed if price is None or not nonnegative(price.get(field))]
        if name == "stt" and stt_shape == "duration":
            if route.get("model") != "gpt-transcribe" or price is None or not nonnegative(price.get("audio_usd_per_minute")) or Decimal(str(price["audio_usd_per_minute"])) <= 0:
                missing.append("qualified-positive-duration-rate-for-exact-gpt-transcribe")
        key_name = route.get("api_key_env", "OPENAI_API_KEY")
        credential_present = bool(environment.get(key_name, "").strip())
        version = price.get("version") if price else None
        route_readiness.append({"role": name, "provider": route.get("provider"), "model": route.get("model"), "pricing_version": version, "missing_rate_fields": missing, "credential_present": credential_present, "ready": bool(price and version and not missing and credential_present)})
    active = ops.counts()
    livekit_host = urllib.parse.urlsplit(environment.get("STUDENT_VOX_LIVEKIT_URL", "")).hostname
    binding = native_binding(ops, sha)
    background_learning_enabled = environment.get("MEMORY_SEMANTIC_LEARNING_ENABLED", "").lower() in ("1", "true", "yes", "on")
    media_enabled = all(environment.get(key, "").lower() in ("1", "true", "yes", "on") for key in ("STUDENT_VOX_ENABLED", "STUDENT_VOX_MEDIA_ENABLED", "STUDENT_VOX_WORKER_QUALIFIED", "STUDENT_VOX_LIVEKIT_CLOUD_QUALIFIED"))
    metered_host_speech = config.get("tts_stream_format", "audio") == "sse" and (config.get("mode") == "cascade" or config.get("fallback_enabled", True))
    transport_priced = nonnegative(policy.get("transport_usd_per_minute")) and bool(policy.get("transport_pricing_version"))
    audio = json.loads((DIRECTORY / "audio-metadata.json").read_text())
    valid_audio = audio.get("fixture_only") is True and len(audio.get("fixtures", [])) == 5
    for fixture in audio.get("fixtures", []):
        identity = fixture.get("id", "")
        if not re.fullmatch("[a-z-]+", identity):
            valid_audio = False
            continue
        filename = DIRECTORY / "fixtures" / (identity + ".wav")
        valid_audio &= filename.is_file() and hashlib.sha256(filename.read_bytes()).hexdigest() == fixture.get("sha256")
        valid_audio &= fixture.get("sample_rate") == 16000 and fixture.get("channels") == 1 and 0 < fixture.get("duration_ms", 0) <= 22000
    playwright = (ROOT / "browser/node_modules/playwright/package.json").is_file()
    image_available = subprocess.run(["docker", "image", "inspect", IMAGE], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
    ready = bool(media_enabled and livekit_host and binding["ready"] and metered_host_speech and valid_audio and playwright and image_available and all(row["ready"] for row in route_readiness) and not active["reserved_total"])
    public = {"sha": sha, "scope": "isolated-test", "readiness_only": True, "provider_calls": 0, "ready": ready, "media_enabled": media_enabled, "native_learning_bound": binding["ready"], "native_learning_artifact": binding, "background_learning_env_enabled_informational_only": background_learning_enabled, "metered_host_speech": metered_host_speech, "transport_priced": transport_priced, "transport_usd_status": "estimate" if transport_priced else "unknown", "transport_usage_limit_ms": 135000, "provider_estimated_spend_stop_usd": 2, "routes": route_readiness, "stt_expected_billing_shape": stt_shape, "active_calls": active, "offline_audio_valid": bool(valid_audio), "cached_browser_ready": playwright and image_available, "policy_revision": policy_row["revision"]}
    private = {"policy_revision": policy_row["revision"], "livekit_host": livekit_host, "transport_pricing_known": transport_priced, "transport_rate": str(policy["transport_usd_per_minute"]) if transport_priced else None, "configuration_hashes": {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in ("runtime.env", "inference.json")}, "policy_hash": hashlib.sha256(json.dumps(policy_row, sort_keys=True).encode()).hexdigest()}
    return public, private


def cookies_for_browser(jar):
    host = urllib.parse.urlsplit(SITE).hostname
    return [{"name": cookie.name, "value": cookie.value, "url": SITE, "httpOnly": cookie.has_nonstandard_attr("HttpOnly"), "secure": cookie.secure, "sameSite": "Lax", **({"expires": cookie.expires} if cookie.expires else {})} for cookie in jar if cookie.domain.lstrip(".") == host]


def revoke_registration_session(opener, jar):
    # Secure cookies are stored but not automatically sent over loopback HTTP.
    # Present this exact registration cookie only for immediate revocation and
    # a negative /me proof; its value never leaves RAM or enters diagnostics.
    cookies = [cookie for cookie in jar if cookie.domain.lstrip(".") == "127.0.0.1"]
    if len(cookies) != 1:
        raise RuntimeError("Exact loopback registration session unavailable")
    cookie = cookies[0]
    private_cookie = cookie.name + "=" + cookie.value
    status, value = api(opener, "/auth/logout", method="POST", body={}, loopback=True,
                        private_loopback_cookie=private_cookie)
    if status != 200 or value != {"status": "signed_out"}:
        raise RuntimeError("Registration session revocation unconfirmed")
    status, _ = api(opener, "/auth/me", loopback=True, private_loopback_cookie=private_cookie)
    if status != 401:
        raise RuntimeError("Registration session remained authorized")
    try:
        jar.clear(cookie.domain, cookie.path, cookie.name)
    except KeyError:
        pass  # The logout response may already have expired this cookie.
    return True


def cleanup(opener, account_key):
    outcome = {"call_stopped": False, "qa_memory_erased": False, "history_disabled": False, "test_session_logged_out": False}
    try:
        context = required(opener, "/vox/calls/context", account=account_key)
        if (context.get("call") or {}).get("status") == "reserved":
            call = context["call"]
            if not re.fullmatch("[a-fA-F0-9-]{36}", call["client_call_id"]):
                raise RuntimeError("Invalid owned cleanup identity")
            required(opener, "/vox/calls/" + call["client_call_id"] + "/end", method="POST", body={"generation": call["generation"]}, account=account_key)
        current = required(opener, "/vox/calls/context", account=account_key)
        outcome["call_stopped"] = not current.get("call") or current["call"]["status"] != "reserved"
        privacy = required(opener, "/memory/privacy", account=account_key)
        required(opener, "/memory/all", method="DELETE", body={"generation": privacy["generation"], "confirmation": "DELETE EVERYTHING"}, account=account_key)
        privacy = required(opener, "/memory/privacy", account=account_key)
        required(opener, "/memory/privacy", method="PUT", body={"history_enabled": False, "retention_days": None, "off_the_record": True, "generation": privacy["generation"], "disclosure_accepted": False}, account=account_key)
        facts = required(opener, "/memory/facts", account=account_key)
        history = required(opener, "/memory/history", account=account_key)
        state = required(opener, "/memory/privacy", account=account_key)
        outcome["qa_memory_erased"] = facts == [] and history == []
        outcome["history_disabled"] = not state["history_enabled"] and state["off_the_record"]
    except Exception as error:
        outcome["failure_class"] = type(error).__name__
    finally:
        try:
            status, _ = api(opener, "/auth/logout", method="POST", body={})
            outcome["test_session_logged_out"] = status == 200
        except Exception as error:
            outcome["logout_failure_class"] = type(error).__name__
    return outcome


def main():
    global LIVE_CALLS_MAY_HAVE_STARTED
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("preflight", "authorized-run"))
    parser.add_argument("sha")
    parser.add_argument("--stt-billing-shape", choices=("tokens", "duration"), default="duration")
    parser.add_argument("--profile", choices=("full-three-calls", "neutral-private-only"), default="full-three-calls")
    parser.add_argument("--prior-provider-usd")
    parser.add_argument("--prior-connected-ms", type=int)
    parser.add_argument("--prior-call-count", type=int)
    parser.add_argument("--root-explicitly-authorized-live-provider-calls", action="store_true")
    arguments = parser.parse_args()
    if not re.fullmatch("[a-f0-9]{40}", arguments.sha):
        raise ValueError("Full immutable source SHA required")
    limits = qualification_limits(arguments.profile, arguments.prior_provider_usd,
                                  arguments.prior_connected_ms, arguments.prior_call_count)
    public, private = preflight(arguments.sha, arguments.stt_billing_shape)
    public["qualification_limits"] = limits
    if arguments.mode == "preflight":
        print(json.dumps(public))
        return
    if not arguments.root_explicitly_authorized_live_provider_calls or not public["ready"]:
        print(json.dumps({**public, "execution_refused": True}))
        raise SystemExit(2)
    # A fresh isolated synthetic account is ordinary test data. No quota is reset,
    # no other account is used and metering rows are retained after cleanup.
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(NoRedirect(), urllib.request.HTTPCookieProcessor(jar))
    secret = {"email": "voice-qualified-" + secrets.token_hex(12) + "@example.com", "password": secrets.token_urlsafe(32), "display_name": "Synthetic spoken voice QA"}
    report = {"sha": arguments.sha, "scope": "isolated-test", "provider_calls_authorized": True,
              "qualification_limits": limits, "pass_scope": "selected-profile-only",
              "source_preflight": public, "registration_session_revoked": False, "passed": False}
    account_key = None
    child = None
    name = None
    try:
        registered = required(opener, "/auth/register", method="POST", body=secret, loopback=True)
        report["registration_session_revoked"] = revoke_registration_session(opener, jar)
        account = required(opener, "/auth/login", method="POST", body={"email": secret["email"], "password": secret["password"]})
        if (registered["id"], registered["workspace"]["id"]) != (account["id"], account["workspace"]["id"]):
            raise RuntimeError("Synthetic account scope changed")
        account_key = account["workspace"]["id"] + ":" + account["id"]
        privacy = required(opener, "/memory/privacy", account=account_key)
        if privacy["history_enabled"]:
            raise RuntimeError("Fresh synthetic account already retained text")
        required(opener, "/memory/privacy", method="PUT", body={"history_enabled": True, "retention_days": 30, "off_the_record": False, "generation": privacy["generation"], "disclosure_accepted": True}, account=account_key)
        payload = {"user_id": account["id"], "workspace_id": account["workspace"]["id"], "cookies": cookies_for_browser(jar), "livekit_host": private["livekit_host"], "policy_revision": private["policy_revision"], "transport_pricing_known": private["transport_pricing_known"], "transport_rate": private["transport_rate"]}
        secret.clear()
        name = PROJECT + "-voice-qualification-" + secrets.token_hex(6)
        command = ["docker", "run", "--rm", "-i", "--name", name, "--network", "host", "--memory", "1g", "--cpus", "2", "--shm-size", "512m", "-v", str(ROOT / "browser") + ":/test", "-w", "/test", IMAGE, "node", "/test/voice-qualification/qualify.cjs", "--authorized-live-voice-qualification", arguments.sha]
        if limits["profile"] == "neutral-private-only":
            command.extend(["--profile", limits["profile"], "--prior-provider-usd", limits["prior_provider_usd"],
                            "--prior-connected-ms", str(limits["prior_connected_ms"]),
                            "--prior-call-count", str(limits["prior_call_count"])])
        LIVE_CALLS_MAY_HAVE_STARTED = True
        child = subprocess.run(command, input=json.dumps(payload), text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=300)
        payload.clear()
        objects = []
        for line in child.stdout.splitlines():
            try:
                value = json.loads(line)
            except (ValueError, TypeError):
                continue
            if isinstance(value, dict) and value.get("schema") == "navox-live-synthetic-voice-qualification.v1":
                objects.append(value)
        if len(objects) != 1:
            raise RuntimeError("Content-free child qualification result unavailable")
        report["browser"] = objects[0]
        report["passed"] = child.returncode == 0 and objects[0].get("passed") is True
    except Exception as error:
        report["failure_class"] = type(error).__name__
    finally:
        if not report["registration_session_revoked"] and any(cookie.domain.lstrip(".") == "127.0.0.1" for cookie in jar):
            try:
                report["registration_session_revoked"] = revoke_registration_session(opener, jar)
            except Exception as error:
                report["registration_cleanup_failure_class"] = type(error).__name__
                report["passed"] = False
        if name:
            # A timeout kills the Docker CLI, not necessarily its container.
            # Stop only this helper before recovering the same owned session.
            try:
                subprocess.run(["docker", "rm", "-f", name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15)
            except Exception as error:
                report["helper_container_cleanup_failure_class"] = type(error).__name__
                report["passed"] = False
        if account_key:
            report["cleanup"] = cleanup(opener, account_key)
            report["passed"] &= all(report["cleanup"].get(field) is True for field in ("call_stopped", "qa_memory_erased", "history_disabled", "test_session_logged_out"))
        import prepare_release as ops
        if account_key:
            import uuid
            owner_id = str(uuid.UUID(account_key.split(":")[1]))
            workspace_id = str(uuid.UUID(account_key.split(":")[0]))
            query = "SELECT COALESCE(json_agg(row_to_json(t)),'[]'::json) FROM (SELECT call_id::text AS call_id, count(*) AS streams, bool_and(status='complete') AS complete, sum(COALESCE((cumulative->>'billed_audio_ms')::bigint,0)) AS billed_audio_ms, sum(COALESCE((cumulative->>'unclassified_input')::bigint,0)+COALESCE((cumulative->>'unclassified_output')::bigint,0)) AS unclassified_tokens FROM vox_usage_streams WHERE kind='provider' AND provider='openai' AND model='gpt-transcribe' AND user_id='" + owner_id + "'::uuid AND workspace_id='" + workspace_id + "'::uuid GROUP BY call_id) t"
            rows = json.loads(ops.psql(query))
            report["actual_stt_duration_receipts"] = [{"native_call_hash": hashlib.sha256(row["call_id"].encode()).hexdigest(), "streams": row["streams"], "complete": row["complete"], "billed_audio_ms": row["billed_audio_ms"], "unclassified_tokens": row["unclassified_tokens"]} for row in rows]
            expected = {call["native_call_hash"] for call in report.get("browser", {}).get("calls", [])}
            observed = {row["native_call_hash"] for row in report["actual_stt_duration_receipts"] if row["complete"] and row["billed_audio_ms"] > 0 and row["unclassified_tokens"] == 0}
            report["duration_billing_verified_for_every_admitted_call"] = bool(expected) and expected == observed
            report["passed"] &= report["duration_billing_verified_for_every_admitted_call"]
            provider_query = "SELECT COALESCE(json_agg(row_to_json(t)),'[]'::json) FROM (SELECT provider,model,status,count(*) AS streams FROM vox_usage_streams WHERE kind='provider' AND user_id='" + owner_id + "'::uuid AND workspace_id='" + workspace_id + "'::uuid GROUP BY provider,model,status ORDER BY provider,model,status) t"
            report["provider_stream_states"] = json.loads(ops.psql(provider_query))
            report["all_provider_streams_complete"] = bool(report["provider_stream_states"]) and all(row["status"] == "complete" for row in report["provider_stream_states"])
            report["passed"] &= report["all_provider_streams_complete"]
        report["private_configuration_unchanged"] = private["configuration_hashes"] == {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in private["configuration_hashes"]}
        policy_row = json.loads(ops.psql("SELECT json_build_object('revision',revision,'definition',definition) FROM vox_usage_policy WHERE id=1"))
        report["policy_unchanged"] = private["policy_hash"] == hashlib.sha256(json.dumps(policy_row, sort_keys=True).encode()).hexdigest()
        report["passed"] &= report["registration_session_revoked"] and report["private_configuration_unchanged"] and report["policy_unchanged"]
        report["source_scope"] = "isolated-test-only; main/public production unchanged"
        report["physical_phone_acceptance"] = False
        destination = DIRECTORY / ("result-" + arguments.sha[:12] + "-" + dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ") + ".json")
        destination.write_text(json.dumps(report, indent=2) + "\n")
        destination.chmod(0o600)
        # Only the content-free report is emitted. Child stderr and response
        # bodies never enter process diagnostics, even when qualification fails.
        print(json.dumps(report))
    raise SystemExit(0 if report["passed"] else 1)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as error:
        print(json.dumps({"ok": False, "failure_class": type(error).__name__, "provider_calls_may_have_started": LIVE_CALLS_MAY_HAVE_STARTED}))
        raise SystemExit(1)
