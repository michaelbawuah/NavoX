import asyncio
from collections.abc import Awaitable, Callable
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from navox.core.settings import Settings, get_settings

router = APIRouter(tags=["health"])
Probe = Callable[[Settings], Awaitable[bool]]
SettingsDependency = Annotated[Settings, Depends(get_settings)]


async def check_database(settings: Settings) -> bool:
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
    finally:
        await engine.dispose()


async def check_temporal(settings: Settings) -> bool:
    host, separator, port_text = settings.temporal_target.partition(":")
    if not separator or not host or not port_text.isdecimal():
        return False
    try:
        _reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, int(port_text)), timeout=1.0
        )
        writer.close()
        await writer.wait_closed()
        return True
    except (OSError, TimeoutError):
        return False


async def readiness(settings: Settings) -> dict[str, bool]:
    database, temporal = await asyncio.gather(check_database(settings), check_temporal(settings))
    return {"database": database, "temporal": temporal}


@router.get("/health/live")
async def live() -> dict[str, str]:
    return {"status": "live", "service": "navox-api", "version": "0.1.0"}


@router.get("/health/ready")
async def ready(settings: SettingsDependency) -> dict[str, object]:
    checks = await readiness(settings)
    if not all(checks.values()):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"status": "not_ready", "checks": checks},
        )
    return {"status": "ready", "service": "navox-api", "version": "0.1.0", "checks": checks}
