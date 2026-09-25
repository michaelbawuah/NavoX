from dataclasses import dataclass
from typing import Literal

RiskLevel = Literal["R0", "R1", "R2", "R3", "R4", "R5"]


@dataclass(frozen=True)
class ActionContract:
    name: str
    provider: str
    risk_level: RiskLevel
    required_permissions: tuple[str, ...]
    idempotency_policy: str
    timeout_seconds: int
    verification_method: str
    executable_in_milestone5: bool


def _contract(
    name: str,
    provider: str,
    risk_level: RiskLevel,
    *,
    permissions: tuple[str, ...] = (),
    idempotency: str = "plan_step",
    timeout_seconds: int = 20,
    verification: str = "persisted_result",
    executable: bool = False,
) -> ActionContract:
    return ActionContract(
        name=name,
        provider=provider,
        risk_level=risk_level,
        required_permissions=permissions,
        idempotency_policy=idempotency,
        timeout_seconds=timeout_seconds,
        verification_method=verification,
        executable_in_milestone5=executable,
    )


ACTION_CONTRACTS: dict[str, ActionContract] = {
    "subscription.cancel": _contract(
        "subscription.cancel",
        "connector",
        "R4",
        permissions=("subscription.cancel",),
        idempotency="navox_at_most_once_no_ambiguous_retry",
        verification="independent_provider_state",
        executable=True,
    ),
    "navox.commitment.inspect": _contract(
        "navox.commitment.inspect",
        "navox",
        "R0",
        executable=True,
        verification="tenant_scoped_database_read",
    ),
    "navox.context.prepare": _contract(
        "navox.context.prepare",
        "navox",
        "R1",
        executable=True,
        verification="context_hash_match",
    ),
    "navox.next_steps.prepare": _contract(
        "navox.next_steps.prepare",
        "navox",
        "R1",
        executable=True,
        verification="persisted_preparation",
    ),
    "gmail.search": _contract(
        "gmail.search",
        "google",
        "R0",
        permissions=("https://www.googleapis.com/auth/gmail.readonly",),
        verification="provider_response",
    ),
    "gmail.read": _contract(
        "gmail.read",
        "google",
        "R0",
        permissions=("https://www.googleapis.com/auth/gmail.readonly",),
        verification="provider_response",
    ),
    "gmail.create_draft": _contract(
        "gmail.create_draft",
        "google",
        "R1",
        permissions=("https://www.googleapis.com/auth/gmail.compose",),
        verification="provider_draft_id",
    ),
    "gmail.send": _contract(
        "gmail.send",
        "google",
        "R3",
        permissions=("https://www.googleapis.com/auth/gmail.send",),
        idempotency="navox_at_most_once_no_ambiguous_retry",
        timeout_seconds=20,
        verification="provider_message_id",
        executable=True,
    ),
    "calendar.search": _contract(
        "calendar.search",
        "google",
        "R0",
        permissions=("https://www.googleapis.com/auth/calendar.readonly",),
        verification="provider_response",
    ),
    "calendar.availability": _contract(
        "calendar.availability",
        "google",
        "R0",
        permissions=("https://www.googleapis.com/auth/calendar.readonly",),
        verification="provider_response",
    ),
    "calendar.create_event": _contract(
        "calendar.create_event",
        "google",
        "R2",
        permissions=("https://www.googleapis.com/auth/calendar.events",),
        verification="provider_event_id",
    ),
    "calendar.update_event": _contract(
        "calendar.update_event",
        "google",
        "R2",
        permissions=("https://www.googleapis.com/auth/calendar.events",),
        verification="provider_event_version",
    ),
    "drive.search": _contract(
        "drive.search",
        "google",
        "R0",
        permissions=("https://www.googleapis.com/auth/drive.readonly",),
        verification="provider_response",
    ),
    "drive.read": _contract(
        "drive.read",
        "google",
        "R0",
        permissions=("https://www.googleapis.com/auth/drive.readonly",),
        verification="provider_response",
    ),
    "drive.create": _contract(
        "drive.create",
        "google",
        "R2",
        permissions=("https://www.googleapis.com/auth/drive.file",),
        verification="provider_file_id",
    ),
    "drive.update": _contract(
        "drive.update",
        "google",
        "R2",
        permissions=("https://www.googleapis.com/auth/drive.file",),
        verification="provider_file_version",
    ),
}


def get_action_contract(name: str) -> ActionContract | None:
    return ACTION_CONTRACTS.get(name)
