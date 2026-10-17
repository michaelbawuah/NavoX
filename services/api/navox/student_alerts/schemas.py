import base64
import re
from urllib.parse import unquote, urlsplit
from uuid import UUID

from cryptography.hazmat.primitives.asymmetric import ec
from pydantic import BaseModel, ConfigDict, Field, field_validator


def decode_key(value: str, length: int) -> bytes:
    try:
        raw = base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
    except (ValueError, UnicodeError) as error:
        raise ValueError("Invalid browser subscription key") from error
    if len(raw) != length:
        raise ValueError("Invalid browser subscription key")
    return raw


def endpoint_origin(value: str) -> str:
    try:
        parsed = urlsplit(value)
        host = (parsed.hostname or "").lower()
        trusted = host in {
            "fcm.googleapis.com",
            "updates.push.services.mozilla.com",
        } or host.endswith((".push.apple.com", ".notify.windows.com"))
        if (
            not trusted
            or parsed.scheme != "https"
            or parsed.port not in {None, 443}
            or parsed.username
            or parsed.password
            or parsed.fragment
            or parsed.query
            or any(part in {".", ".."} for part in unquote(parsed.path).split("/"))
            or any(ord(char) <= 32 or ord(char) == 127 for char in unquote(value))
            or not parsed.path.startswith("/")
            or "\\" in value
            or "%" in host
            or any(ord(char) <= 32 or ord(char) == 127 for char in value)
        ):
            raise ValueError
        return f"https://{host}"
    except (ValueError, UnicodeError):
        raise ValueError("This browser's push service is not supported") from None


class AlertSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = False
    next_class: bool = True
    leave_now: bool = False
    lead_minutes: int = Field(default=15, ge=1, le=60)
    quiet_start: str = "23:00"
    quiet_end: str = "07:00"
    daily_cap: int = Field(default=12, ge=1, le=12)

    @field_validator("quiet_start", "quiet_end")
    @classmethod
    def valid_time(cls, value: str) -> str:
        if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", value):
            raise ValueError("Choose a time from 00:00 to 23:59")
        return value


class SubscriptionKeys(BaseModel):
    model_config = ConfigDict(extra="forbid")
    p256dh: str = Field(min_length=86, max_length=90)
    auth: str = Field(min_length=22, max_length=24)

    @field_validator("p256dh")
    @classmethod
    def valid_public(cls, value: str) -> str:
        raw = decode_key(value, 65)
        ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), raw)
        return value

    @field_validator("auth")
    @classmethod
    def valid_auth(cls, value: str) -> str:
        decode_key(value, 16)
        return value


class SubscriptionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    endpoint: str = Field(min_length=10, max_length=4096)
    keys: SubscriptionKeys
    expirationTime: int | None = None

    @field_validator("endpoint")
    @classmethod
    def valid_endpoint(cls, value: str) -> str:
        endpoint_origin(value)
        return value


class SubscriptionResponse(BaseModel):
    id: UUID


class DeviceStatus(BaseModel):
    id: UUID
    endpoint_hash: str


class AlertStatus(BaseModel):
    configured: bool
    public_key: str | None
    settings: AlertSettings
    devices: list[DeviceStatus]
    timezone: str
    notice: str
