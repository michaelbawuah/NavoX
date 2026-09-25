"""Fixed-origin Stripe sandbox protocol. Live keys/objects are never accepted.

Stripe's DELETE is idempotent but has no documented If-Match precondition.
This adapter is deliberately a sandbox integration, not a live cancellation
profile. The R4 engine provides the durable submission fence and never retries
the write. A full snapshot fingerprint detects changes observed before dispatch.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, JsonValue

from navox.connectors.builtin.generic_api import _contains_credential
from navox.connectors.contracts import (
    AuthorizationRequest,
    AuthorizationResult,
    CanonicalResource,
    ConnectorActionRequest,
    ConnectorActionResult,
    ConnectorConnectionContext,
    ConnectorHealth,
    ConnectorManifest,
    ConnectorRuntimeError,
    FetchResourceRequest,
    SecretAccessor,
    SyncPage,
    SyncRequest,
)
from navox.connectors.outbound import ApprovedHTTPSTransport
from navox.subscriptions.cancellation_contracts import CancellationEconomics

ORIGIN = "https://api.stripe.com"
API_VERSION: Literal["2026-08-26.dahlia"] = "2026-08-26.dahlia"
CONNECTOR_ID = "stripe-sandbox-subscription"
PROVIDER = "stripe_sandbox"
READ = "subscription.read"
CANCEL = "subscription.cancel"
SECRET_NAME = "STRIPE_TEST_KEY"
MAX_BYTES = 128_000

MANIFEST = ConnectorManifest.model_validate(
    {
        "id": CONNECTOR_ID,
        "version": "1.0.0",
        "displayName": "Stripe sandbox subscription",
        "category": "developer",
        "connectorClass": "TOKEN_API",
        "auth": [{"kind": "api_token", "label": "Stripe sandbox key", "scopes": []}],
        "resourceTypes": ["stripe.subscription"],
        "capabilities": {
            "read": [
                {"name": READ, "description": "Inspect one sandbox subscription", "sensitive": True}
            ],
            "write": [
                {
                    "name": CANCEL,
                    "description": "Cancel one sandbox subscription after exact approval",
                    "sensitive": True,
                }
            ],
            "events": [],
            "incrementalSync": False,
        },
        "requiredSecrets": [SECRET_NAME],
        "rateLimitStrategy": "provider_headers",
        "minimumNavoxConnectorApiVersion": "1",
    }
)


def denied() -> ConnectorRuntimeError:
    return ConnectorRuntimeError("PERMISSION_DENIED", "Stripe sandbox access is not authorized")


def invalid() -> ConnectorRuntimeError:
    return ConnectorRuntimeError("INVALID_PROVIDER_RESPONSE", "Stripe sandbox response is invalid")


def unsupported() -> ConnectorRuntimeError:
    return ConnectorRuntimeError(
        "UNSUPPORTED_CAPABILITY",
        "Use a simple active USD sandbox subscription without tax or discounts",
    )


def fingerprint(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def identifier(value: object, prefix: str) -> str:
    if not isinstance(value, str) or re.fullmatch(prefix + r"_[A-Za-z0-9]{1,120}", value) is None:
        raise invalid()
    return value


def test_key(value: str) -> str:
    if re.fullmatch(r"(?:rk|sk)_test_[A-Za-z0-9]{8,240}", value) is None:
        raise denied()
    return value


class StripeBinding(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    account_id: str = Field(pattern=r"^acct_[A-Za-z0-9]{1,120}$")
    subscription_id: str = Field(pattern=r"^sub_[A-Za-z0-9]{1,120}$")
    customer_id: str = Field(pattern=r"^cus_[A-Za-z0-9]{1,120}$")
    api_version: Literal["2026-08-26.dahlia"] = API_VERSION
    managed_by_source_workflow: Literal[True] = True


class StripeState(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    subscription_id: str
    customer_id: str
    revision: str
    status: Literal["active", "canceled"]
    economics: CancellationEconomics


def timestamp(value: object) -> datetime:
    if type(value) is not int or not 0 < value < 253402300800:
        raise invalid()
    return datetime.fromtimestamp(value, UTC)


def parse_subscription(payload: dict[str, object], subscription_id: str) -> StripeState:
    if (
        payload.get("id") != subscription_id
        or payload.get("object") != "subscription"
        or payload.get("livemode") is not False
    ):
        raise invalid()
    customer_id = identifier(payload.get("customer"), "cus")
    status = payload.get("status")
    if status not in {"active", "canceled"}:
        raise unsupported()
    # Keep the first real-provider test narrow enough to show exact known economics.
    tax = payload.get("automatic_tax")
    if (
        not isinstance(tax, dict)
        or tax.get("enabled") is not False
        or payload.get("currency") != "usd"
        or payload.get("collection_method") != "charge_automatically"
        or type(payload.get("cancel_at_period_end")) is not bool
        or any(
            payload.get(key)
            for key in (
                "discount",
                "discounts",
                "default_tax_rates",
                "schedule",
                "pending_update",
                "pause_collection",
                "transfer_data",
                "on_behalf_of",
                "application_fee_percent",
                "pending_invoice_item_interval",
                "next_pending_invoice_item_invoice",
                "trial_end",
            )
        )
    ):
        raise unsupported()
    items = payload.get("items")
    if not isinstance(items, dict) or items.get("has_more") is not False:
        raise unsupported()
    data = items.get("data")
    if not isinstance(data, list) or len(data) != 1 or not isinstance(data[0], dict):
        raise unsupported()
    item = data[0]
    price = item.get("price")
    if (
        not isinstance(price, dict)
        or price.get("object") != "price"
        or price.get("livemode") is not False
    ):
        raise invalid()
    price_id = identifier(price.get("id"), "price")
    identifier(price.get("product"), "prod")
    recurring = price.get("recurring")
    amount, quantity = price.get("unit_amount"), item.get("quantity")
    if (
        price.get("currency") != "usd"
        or price.get("billing_scheme") != "per_unit"
        or price.get("type") != "recurring"
        or price.get("transform_quantity")
        or price.get("custom_unit_amount")
        or price.get("tiers_mode")
        or item.get("tax_rates")
        or item.get("discounts")
        or not isinstance(recurring, dict)
        or recurring.get("usage_type") != "licensed"
        or type(amount) is not int
        or not 0 <= amount <= 99_999_999
        or type(quantity) is not int
        or not 1 <= quantity <= 1000
        or type(recurring.get("interval_count")) is not int
    ):
        raise unsupported()
    interval, count = recurring.get("interval"), recurring.get("interval_count")
    decimal_amount = price.get("unit_amount_decimal")
    if (
        not isinstance(interval, str)
        or interval not in {"day", "week", "month", "year"}
        or not isinstance(count, int)
        or not 1 <= count <= 365
        or not isinstance(decimal_amount, str)
        or re.fullmatch(r"\d+(?:\.0+)?", decimal_amount) is None
        or Decimal(decimal_amount) != amount
    ):
        raise unsupported()
    renewal = timestamp(item.get("current_period_end"))
    if status == "active" and (
        payload.get("cancel_at_period_end")
        or payload.get("cancel_at")
        or payload.get("canceled_at")
    ):
        raise unsupported()
    if status == "canceled":
        timestamp(payload.get("ended_at"))
    return StripeState(
        subscription_id=subscription_id,
        customer_id=customer_id,
        revision=fingerprint(payload),
        status="active" if status == "active" else "canceled",
        economics=CancellationEconomics(
            plan_name=price_id,
            billing_amount=Decimal(amount) * quantity / 100,
            billing_currency="USD",
            billing_interval=interval.upper(),
            billing_interval_count=count,
            next_renewal_at=renewal if status == "active" else None,
            auto_renew=status == "active",
        ),
    )


async def request(
    token: str,
    path: str,
    *,
    cancel: bool = False,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, object]:
    token = test_key(token)
    is_subscription = re.fullmatch(r"/v1/subscriptions/sub_[A-Za-z0-9]{1,120}", path) is not None
    if (not is_subscription and path != "/v1/account") or (cancel and not is_subscription):
        raise denied()
    try:
        async with httpx.AsyncClient(
            transport=transport
            or ApprovedHTTPSTransport(
                ORIGIN, allowed_delete_paths=frozenset({path}) if cancel else frozenset()
            ),
            timeout=30,
            trust_env=False,
            follow_redirects=False,
            headers={
                "Authorization": f"Bearer {token}",
                "Stripe-Version": API_VERSION,
                "Accept": "application/json",
                "Accept-Encoding": "identity",
            },
        ) as client:
            # DELETE ignores Idempotency-Key. Do not pretend it provides an If-Match check.
            async with client.stream(
                "DELETE" if cancel else "GET",
                ORIGIN + path,
                params={"invoice_now": "false", "prorate": "false"} if cancel else None,
            ) as response:
                if response.status_code in {401, 403}:
                    raise denied()
                if response.status_code != 200:
                    raise ConnectorRuntimeError(
                        "PROVIDER_UNAVAILABLE", "Stripe sandbox request failed"
                    )
                if response.headers.get("content-encoding", "identity") not in {"", "identity"}:
                    raise invalid()
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    if len(body) + len(chunk) > MAX_BYTES:
                        raise invalid()
                    body.extend(chunk)
                try:
                    raw = json.loads(body)
                except (ValueError, UnicodeError):
                    raise invalid() from None
                if not isinstance(raw, dict) or _contains_credential(raw, token):
                    raise invalid()
                return {str(key): value for key, value in raw.items()}
    except (httpx.HTTPError, TimeoutError, OSError):
        raise ConnectorRuntimeError(
            "PROVIDER_UNAVAILABLE", "Stripe sandbox request failed"
        ) from None


async def inspect(
    token: str, binding: StripeBinding, *, transport: httpx.AsyncBaseTransport | None = None
) -> StripeState:
    account = await request(token, "/v1/account", transport=transport)
    if account.get("object") != "account" or account.get("id") != binding.account_id:
        raise denied()
    state = parse_subscription(
        await request(token, f"/v1/subscriptions/{binding.subscription_id}", transport=transport),
        binding.subscription_id,
    )
    if state.customer_id != binding.customer_id:
        raise denied()
    return state


class StripeSandboxConnector:
    """Health-only SDK adapter; importing is explicit and cancellation uses SPEC-004."""

    def __init__(self, config: Mapping[str, JsonValue], secrets: SecretAccessor | None) -> None:
        self.binding = StripeBinding.model_validate(config)
        self.secrets = secrets

    def get_manifest(self) -> ConnectorManifest:
        return MANIFEST

    async def authorize(self, context: AuthorizationRequest) -> AuthorizationResult:
        del context
        return AuthorizationResult(authorized=False)

    async def health(self, connection: ConnectorConnectionContext) -> ConnectorHealth:
        if self.secrets is None or READ not in connection.authorized_capabilities:
            raise denied()
        await inspect(self.secrets.get(SECRET_NAME), self.binding)
        return ConnectorHealth(state="CONNECTED", checked_at=datetime.now(UTC))

    async def sync(self, request: SyncRequest) -> SyncPage:
        del request
        raise ConnectorRuntimeError(
            "UNSUPPORTED_CAPABILITY", "Sandbox subscriptions use explicit import"
        )

    async def fetch_resource(self, request: FetchResourceRequest) -> CanonicalResource:
        del request
        raise ConnectorRuntimeError("UNSUPPORTED_CAPABILITY", "Use the scoped subscription review")

    async def execute(self, request: ConnectorActionRequest) -> ConnectorActionResult:
        del request
        raise ConnectorRuntimeError(
            "UNSUPPORTED_CAPABILITY", "Cancellation requires the SPEC-004 exact approval"
        )
