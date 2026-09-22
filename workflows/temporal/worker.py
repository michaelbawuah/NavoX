"""Compatibility entrypoint for the packaged NavoX Temporal worker."""

import asyncio

from navox.workflows.worker import main


if __name__ == "__main__":
    asyncio.run(main())
