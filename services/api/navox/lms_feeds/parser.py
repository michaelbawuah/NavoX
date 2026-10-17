"""Kill a pathological calendar parser; a timeout never leaves a worker running."""

import asyncio
import base64
import json
import os
import re
import sys
from datetime import datetime

from navox.lms_feeds.schemas import ParsedFeed, Platform
from navox.lms_feeds.transport import MAX_FEED_BYTES, FeedError

_slots = asyncio.Semaphore(2)


async def parse_bounded(
    content: bytes,
    *,
    timezone: str,
    all_day_due_time: str,
    now: datetime,
    private_url: str,
    platform: Platform,
) -> ParsedFeed:
    if len(content) > MAX_FEED_BYTES:
        raise FeedError("feed_too_large")
    payload = json.dumps(
        {
            "content": base64.b64encode(content).decode("ascii"),
            "timezone": timezone,
            "all_day_due_time": all_day_due_time,
            "now": now.isoformat(),
            "private_url": private_url,
            "platform": platform,
        }
    ).encode()
    async with _slots:
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "navox.lms_feeds.parse_child",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env={"PATH": os.defpath, "LANG": "C.UTF-8"},
        )
        try:
            async with asyncio.timeout(6):
                output, _ = await process.communicate(payload)
            if process.returncode == 2 and len(output) <= 200:
                failure = json.loads(output or b"{}")
                code = failure.get("error") if isinstance(failure, dict) else None
                if isinstance(code, str) and re.fullmatch(r"[a-z_]{1,64}", code):
                    raise FeedError(code)
            if process.returncode != 0 or len(output) > 20 * 1024 * 1024:
                raise FeedError("invalid_or_complex_calendar")
            return ParsedFeed.model_validate_json(output)
        except FeedError:
            raise
        except (TimeoutError, ValueError):
            raise FeedError("invalid_or_complex_calendar") from None
        finally:
            if process.returncode is None:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
                await process.wait()
