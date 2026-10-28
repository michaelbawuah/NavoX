"""Verify staged app authentication and the exact legacy Google callback bridge.

Run on the VPS. Creates and removes only its own disposable account; emits no
credentials, cookies, session values, email addresses or OAuth parameters.
"""

import http.cookiejar
import json
import secrets
import subprocess
import urllib.request
from uuid import UUID

ORIGIN = "https://app.navox.net"
LANDING = "https://navox-landing.vercel.app"


def main():
    jar = http.cookiejar.CookieJar()
    client = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    address = "landing-cutover-" + secrets.token_hex(12) + "@example.com"
    password = secrets.token_urlsafe(32)
    account = None

    def post(route, payload):
        request = urllib.request.Request(
            ORIGIN + route,
            data=json.dumps(payload).encode(),
            method="POST",
            headers={"Content-Type": "application/json", "Origin": ORIGIN},
        )
        return client.open(request, timeout=15)

    try:
        with post(
            "/api/v1/auth/register",
            {
                "email": address,
                "password": password,
                "display_name": "Landing cutover QA",
            },
        ) as response:
            assert response.status == 201
            account = json.loads(response.read())
        with post("/api/v1/auth/logout", {}) as response:
            assert response.status == 200
        with post("/api/v1/auth/login", {"email": address, "password": password}) as response:
            assert response.status == 200
            assert response.headers.get("Access-Control-Allow-Origin") == ORIGIN
        with client.open(ORIGIN + "/api/v1/auth/me", timeout=15) as response:
            assert response.status == 200
            assert json.loads(response.read())["id"] == account["id"]
        assert any(cookie.secure and cookie.domain == "app.navox.net" for cookie in jar)
        with client.open(
            LANDING + "/api/v1/connections/google/callback?error=access_denied", timeout=15
        ) as response:
            assert response.status == 200
            assert response.geturl().startswith(ORIGIN + "/?google_connection=cancelled")
        print(
            "App registration, login, secure session, account read and legacy callback bridge pass."
        )
    finally:
        if account:
            workspace = str(UUID(account["workspace"]["id"]))
            user = str(UUID(account["id"]))
            sql = (
                f"BEGIN; DELETE FROM workspaces WHERE id='{workspace}'; "
                f"DELETE FROM users WHERE id='{user}'; COMMIT;"
            )
            result = subprocess.run(
                [
                    "docker",
                    "exec",
                    "-i",
                    "navox-postgres-1",
                    "sh",
                    "-c",
                    'exec psql -v ON_ERROR_STOP=1 -U "${POSTGRES_USER:-postgres}" -d navox',
                ],
                input=sql,
                text=True,
                capture_output=True,
            )
            if result.returncode:
                raise RuntimeError("Disposable app account cleanup failed")
            print("Disposable app account and workspace removed.")


if __name__ == "__main__":
    main()
