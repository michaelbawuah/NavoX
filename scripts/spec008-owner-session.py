#!/usr/bin/env python3
"""Create a private local SPEC-008 acceptance session through normal login."""

import getpass
import http.cookiejar
import json
import os
from pathlib import Path
import sys
import tempfile
import urllib.error
import urllib.request

EMAIL = "michaelbaffourawuah706@gmail.com"
EXPECTED_USER = "b1842045-a1a1-499b-ae41-f53bcb99d0f9"
EXPECTED_WORKSPACE = "aad0f47a-4948-44df-8e52-60f6d7a918e3"
DESTINATION = Path("/private/tmp/navox-spec008-owner-auth.json")
BASE_URL = "http://127.0.0.1:8000/api/v1"


def main():
    if not sys.stdin.isatty():
        raise SystemExit("Run this helper in your Mac's Terminal.")
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}), urllib.request.HTTPCookieProcessor(jar)
    )
    password = getpass.getpass(f"NavoX password for {EMAIL}: ")
    request = urllib.request.Request(
        f"{BASE_URL}/auth/login",
        data=json.dumps({"email": EMAIL, "password": password}).encode(),
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
        or verified.get("email") != EMAIL
        or verified.get("id") != EXPECTED_USER
        or verified.get("workspace", {}).get("id") != EXPECTED_WORKSPACE
    ):
        raise SystemExit("Account/workspace did not match the approved owner scope.")
    cookies = [cookie for cookie in jar if cookie.name == "navox_session"]
    if len(cookies) != 1:
        raise SystemExit("A valid NavoX session was not returned.")
    payload = {
        "cookie": f"navox_session={cookies[0].value}",
        "user_id": EXPECTED_USER,
        "workspace_id": EXPECTED_WORKSPACE,
        "email": EMAIL,
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
