#!/usr/bin/env python3
"""Create a private local acceptance session for explicitly supplied owner scope.

Pass --email, --user-id and --workspace-id, or set the corresponding
NAVOX_ACCEPTANCE_EMAIL, NAVOX_ACCEPTANCE_USER_ID and NAVOX_ACCEPTANCE_WORKSPACE_ID
environment variables. These inputs select the account to verify; they do not
grant provider permissions or approve any action.
"""

import argparse
import getpass
import http.cookiejar
import json
import os
from pathlib import Path
import sys
import tempfile
import urllib.error
import urllib.request
from uuid import UUID

DESTINATION = Path("/private/tmp/navox-spec008-owner-auth.json")
BASE_URL = "http://127.0.0.1:8000/api/v1"


def owner_arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--email", default=os.environ.get("NAVOX_ACCEPTANCE_EMAIL"))
    parser.add_argument("--user-id", default=os.environ.get("NAVOX_ACCEPTANCE_USER_ID"))
    parser.add_argument(
        "--workspace-id", default=os.environ.get("NAVOX_ACCEPTANCE_WORKSPACE_ID")
    )
    args = parser.parse_args(argv)
    for field in ("email", "user_id", "workspace_id"):
        value = getattr(args, field)
        if not value or not value.strip():
            parser.error(f"--{field.replace('_', '-')} or its environment variable is required")
    for field in ("user_id", "workspace_id"):
        try:
            setattr(args, field, str(UUID(getattr(args, field))))
        except ValueError:
            parser.error(f"--{field.replace('_', '-')} must be a UUID")
    return args


def main(argv=None):
    args = owner_arguments(argv)
    if not sys.stdin.isatty():
        raise SystemExit("Run this helper in your Mac's Terminal.")
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}), urllib.request.HTTPCookieProcessor(jar)
    )
    password = getpass.getpass(f"NavoX password for {args.email}: ")
    request = urllib.request.Request(
        f"{BASE_URL}/auth/login",
        data=json.dumps({"email": args.email, "password": password}).encode(),
        headers={"Content-Type": "application/json"},
    )
    del password
    try:
        with opener.open(request, timeout=15) as response:
            account = json.load(response)
        with opener.open(f"{BASE_URL}/auth/me", timeout=15) as response:
            verified = json.load(response)
    except urllib.error.HTTPError as error:
        raise SystemExit(f"Sign-in failed (HTTP {error.code}). No session exported.") from None
    except Exception:
        raise SystemExit("NavoX could not be reached. No session exported.") from None
    if (
        account != verified
        or verified.get("email") != args.email
        or verified.get("id") != args.user_id
        or verified.get("workspace", {}).get("id") != args.workspace_id
    ):
        raise SystemExit("Account/workspace did not match the approved owner scope.")
    cookies = [cookie for cookie in jar if cookie.name == "navox_session"]
    if len(cookies) != 1:
        raise SystemExit("A valid NavoX session was not returned.")
    payload = {
        "cookie": f"navox_session={cookies[0].value}",
        "user_id": args.user_id,
        "workspace_id": args.workspace_id,
        "email": args.email,
    }
    with tempfile.NamedTemporaryFile(
        mode="w", prefix="navox-owner-session-", dir=DESTINATION.parent,
        delete=False, encoding="utf-8"
    ) as output:
        os.fchmod(output.fileno(), 0o600)
        json.dump(payload, output)
        output.write("\n")
        temporary = output.name
    os.replace(temporary, DESTINATION)
    print("Signed in. The private session is ready for controlled SPEC-008 checks.")
    print("Your password was not saved. Do not paste session contents into chat.")


if __name__ == "__main__":
    main()
