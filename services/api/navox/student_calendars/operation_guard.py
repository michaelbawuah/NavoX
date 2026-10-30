"""Optional host authority composed into existing calendar boundaries.

Browser operations keep their existing session guard. A voice caller additionally
binds the operation to its exact active call/media epoch. Context-local storage
cannot confer authority across concurrent calls or browser requests.
"""

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from contextvars import ContextVar

Check = Callable[[], Awaitable[None]]
_guard: ContextVar[Check | None] = ContextVar("calendar_operation_guard", default=None)


async def check_operation() -> None:
    check = _guard.get()
    if check is not None:
        await check()


@asynccontextmanager
async def guarded_operation(check: Check) -> AsyncIterator[None]:
    previous = _guard.set(check)
    try:
        await check()
        yield
        await check()
    finally:
        _guard.reset(previous)
