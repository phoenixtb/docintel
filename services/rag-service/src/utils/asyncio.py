"""
Async utilities shared across RAGService and API handlers.
"""

import asyncio
import contextvars
from typing import Callable


async def _run_db(fn: Callable):
    """Run a synchronous DB call in the default executor, preserving contextvars for RLS."""
    ctx = contextvars.copy_context()
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, ctx.run, fn)
