import httpx
import pytest

from navox.connectors.contracts import ConnectorRuntimeError
from navox.connectors.outbound import ApprovedHTTPSTransport, approved_origin, public_addresses


@pytest.mark.parametrize(
    "origin",
    [
        "http://canvas.example.edu",
        "https://127.0.0.1",
        "https://8.8.8.8",
        "https://[::1]",
        "https://canvas.example.edu:8443",
        "https://user:pass@canvas.example.edu",
        "https://canvas.example.edu/api",
        "https://canvas.example.edu?key=x",
        "https://canvas.example.edu#x",
        "https://canvas.example.edu.",
        "https://canvas.local",
        "https://localhost",
        "https://canvas.example.edu\n",
    ],
)
def test_origin_requires_explicit_clean_public_hostname(origin):
    with pytest.raises(ValueError):
        approved_origin(origin)


@pytest.mark.parametrize(
    "addresses",
    [
        [],
        ["127.0.0.1"],
        ["169.254.169.254"],
        ["10.0.0.1"],
        ["8.8.8.8", "192.168.1.1"],
        ["::1"],
        ["::ffff:127.0.0.1"],
        ["64:ff9b::7f00:1"],
        ["ff02::1"],
        ["2002:7f00:1::"],
        ["bad"],
    ],
)
def test_dns_never_selects_public_subset_of_mixed_private_answer(addresses):
    with pytest.raises(ConnectorRuntimeError):
        public_addresses(addresses)


@pytest.mark.asyncio
async def test_transport_pins_public_address_preserves_tls_host_and_drops_cookies():
    seen = []

    async def resolver(host):
        assert host == "canvas.example.edu"
        return ["8.8.8.8"]

    async def handle(request):
        seen.append(request)
        assert request.url.host == "8.8.8.8"
        assert request.headers["host"] == "canvas.example.edu"
        assert request.extensions["sni_hostname"] == "canvas.example.edu"
        assert request.headers["accept-encoding"] == "identity"
        assert "cookie" not in request.headers
        return httpx.Response(200, json=[{"id": 1}], headers={"set-cookie": "private=x"})

    async with httpx.AsyncClient(
        transport=ApprovedHTTPSTransport(
            "https://canvas.example.edu", resolver=resolver, transport=httpx.MockTransport(handle)
        ),
        trust_env=False,
    ) as client:
        response = await client.get(
            "https://canvas.example.edu/api/v1/courses", headers={"cookie": "session=x"}
        )
    assert response.json() == [{"id": 1}]
    assert "set-cookie" not in response.headers
    assert len(seen) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "target",
    [
        "https://evil.example/api",
        "https://canvas.example.edu:8443/api",
        "https://canvas.example.edu/api?access_token=SECRET",
    ],
)
async def test_wrong_origin_or_credential_url_never_reaches_dns(target):
    async def resolver(_):
        raise AssertionError("DNS must not be reached")

    async with httpx.AsyncClient(
        transport=ApprovedHTTPSTransport("https://canvas.example.edu", resolver=resolver)
    ) as client:
        with pytest.raises(ConnectorRuntimeError):
            await client.get(target)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["redirect", "compressed", "oversized", "exception"])
async def test_untrusted_response_rejected_without_disclosing_provider_detail(kind):
    async def resolver(_):
        return ["8.8.8.8"]

    async def handle(request):
        if kind == "redirect":
            return httpx.Response(302, headers={"location": "https://127.0.0.1/"})
        if kind == "compressed":
            return httpx.Response(
                200, headers={"content-encoding": "gzip"}, stream=httpx.ByteStream(b"unread")
            )
        if kind == "oversized":
            return httpx.Response(200, content=b"x" * 2_000_001)
        raise httpx.ConnectError("SECRET-SENTINEL", request=request)

    async with httpx.AsyncClient(
        transport=ApprovedHTTPSTransport(
            "https://canvas.example.edu", resolver=resolver, transport=httpx.MockTransport(handle)
        )
    ) as client:
        with pytest.raises(ConnectorRuntimeError) as caught:
            await client.get("https://canvas.example.edu/api/v1/courses")
    assert "SECRET-SENTINEL" not in str(caught.value)


@pytest.mark.asyncio
async def test_rebinding_after_first_request_is_blocked_on_next_request():
    answers = iter([["8.8.8.8"], ["127.0.0.1"]])
    calls = []

    async def resolver(_):
        return next(answers)

    async def handle(request):
        calls.append(request)
        return httpx.Response(200, json=[])

    async with httpx.AsyncClient(
        transport=ApprovedHTTPSTransport(
            "https://canvas.example.edu", resolver=resolver, transport=httpx.MockTransport(handle)
        )
    ) as client:
        await client.get("https://canvas.example.edu/api/v1/courses")
        with pytest.raises(ConnectorRuntimeError):
            await client.get("https://canvas.example.edu/api/v1/courses?page=2")
    assert len(calls) == 1
