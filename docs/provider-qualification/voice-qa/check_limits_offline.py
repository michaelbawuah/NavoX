"""Exercise continuation limits without importing ops or making any request."""

import http.cookiejar

import run_on_isolated as helper
from run_on_isolated import qualification_limits


default = qualification_limits("full-three-calls")
assert default["max_calls"] == default["total_call_ceiling"] == 3
continued = qualification_limits("neutral-private-only", "0.125", 27800, 1)
assert continued["max_calls"] == 2
assert continued["max_calls"] + continued["prior_call_count"] == 3
assert continued["remaining_transport_ms"] + continued["prior_connected_ms"] == 135000
assert continued["remaining_provider_usd"] == "1.875"

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
):
    try:
        qualification_limits(*args)
    except ValueError:
        continue
    raise AssertionError("Unsafe continuation input accepted")

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
print('{"offline_limits_and_registration_revocation": "pass", "provider_calls": 0}')
