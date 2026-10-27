"""Server-bound provider transports; model arguments never supply credentials."""

from dataclasses import dataclass, field
from typing import Any, Protocol

from navox.student_calendars.provider import CalendarCreateReceipt, CalendarTarget, Check


@dataclass(frozen=True)
class AppleWriteCredential:
    apple_id: str = field(repr=False)
    app_password: str = field(repr=False)


WriteCredential = str | AppleWriteCredential


class BearerWriteProvider(Protocol):
    async def owned_calendars(
        self, *, access_token: str, check: Check
    ) -> tuple[CalendarTarget, ...]: ...
    async def target(
        self, *, access_token: str, calendar_id: str, check: Check
    ) -> CalendarTarget: ...
    async def event(
        self, *, access_token: str, calendar_id: str, event_id: str, check: Check
    ) -> dict[str, Any] | None: ...
    async def insert(
        self, *, access_token: str, calendar_id: str, event: dict[str, Any], check: Check
    ) -> dict[str, Any]: ...
    async def delete(
        self, *, access_token: str, calendar_id: str, event_id: str, etag: str, check: Check
    ) -> None: ...


class AppleWriteProvider(Protocol):
    async def owned_calendars(
        self, *, apple_id: str, app_password: str, check: Check
    ) -> tuple[CalendarTarget, ...]: ...
    async def target(
        self, *, apple_id: str, app_password: str, calendar_id: str, check: Check
    ) -> CalendarTarget: ...
    async def event(
        self, *, apple_id: str, app_password: str, calendar_id: str, event_id: str, check: Check
    ) -> dict[str, Any] | None: ...
    async def insert(
        self,
        *,
        apple_id: str,
        app_password: str,
        calendar_id: str,
        event: dict[str, Any],
        check: Check,
    ) -> CalendarCreateReceipt: ...
    async def delete(
        self,
        *,
        apple_id: str,
        app_password: str,
        calendar_id: str,
        event_id: str,
        etag: str,
        check: Check,
    ) -> None: ...


class BoundWriteProvider(Protocol):
    async def owned_calendars(self, *, check: Check) -> tuple[CalendarTarget, ...]: ...
    async def target(self, *, calendar_id: str, check: Check) -> CalendarTarget: ...
    async def event(
        self, *, calendar_id: str, event_id: str, check: Check
    ) -> dict[str, Any] | None: ...
    async def insert(
        self, *, calendar_id: str, event: dict[str, Any], check: Check
    ) -> CalendarCreateReceipt: ...
    async def delete(self, *, calendar_id: str, event_id: str, etag: str, check: Check) -> None: ...


class BoundBearerWriter:
    def __init__(self, provider: BearerWriteProvider, token: str):
        self.provider, self._token = provider, token

    async def owned_calendars(self, *, check: Check) -> tuple[CalendarTarget, ...]:
        return await self.provider.owned_calendars(access_token=self._token, check=check)

    async def target(self, *, calendar_id: str, check: Check) -> CalendarTarget:
        return await self.provider.target(
            access_token=self._token, calendar_id=calendar_id, check=check
        )

    async def event(
        self, *, calendar_id: str, event_id: str, check: Check
    ) -> dict[str, Any] | None:
        return await self.provider.event(
            access_token=self._token, calendar_id=calendar_id, event_id=event_id, check=check
        )

    async def insert(
        self, *, calendar_id: str, event: dict[str, Any], check: Check
    ) -> CalendarCreateReceipt:
        event = await self.provider.insert(
            access_token=self._token, calendar_id=calendar_id, event=event, check=check
        )
        etag = event.get("etag")
        return CalendarCreateReceipt(event, etag if isinstance(etag, str) else None)

    async def delete(self, *, calendar_id: str, event_id: str, etag: str, check: Check) -> None:
        await self.provider.delete(
            access_token=self._token,
            calendar_id=calendar_id,
            event_id=event_id,
            etag=etag,
            check=check,
        )


class BoundAppleWriter:
    def __init__(self, provider: AppleWriteProvider, credential: AppleWriteCredential):
        self.provider, self._credential = provider, credential

    async def owned_calendars(self, *, check: Check) -> tuple[CalendarTarget, ...]:
        return await self.provider.owned_calendars(
            apple_id=self._credential.apple_id,
            app_password=self._credential.app_password,
            check=check,
        )

    async def target(self, *, calendar_id: str, check: Check) -> CalendarTarget:
        return await self.provider.target(
            apple_id=self._credential.apple_id,
            app_password=self._credential.app_password,
            calendar_id=calendar_id,
            check=check,
        )

    async def event(
        self, *, calendar_id: str, event_id: str, check: Check
    ) -> dict[str, Any] | None:
        return await self.provider.event(
            apple_id=self._credential.apple_id,
            app_password=self._credential.app_password,
            calendar_id=calendar_id,
            event_id=event_id,
            check=check,
        )

    async def insert(
        self, *, calendar_id: str, event: dict[str, Any], check: Check
    ) -> CalendarCreateReceipt:
        return await self.provider.insert(
            apple_id=self._credential.apple_id,
            app_password=self._credential.app_password,
            calendar_id=calendar_id,
            event=event,
            check=check,
        )

    async def delete(self, *, calendar_id: str, event_id: str, etag: str, check: Check) -> None:
        await self.provider.delete(
            apple_id=self._credential.apple_id,
            app_password=self._credential.app_password,
            calendar_id=calendar_id,
            event_id=event_id,
            etag=etag,
            check=check,
        )
