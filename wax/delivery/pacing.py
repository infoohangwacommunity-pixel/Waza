"""
Sequential message pacing for multi-chunk tutor replies.

Sends bubbles with small delays so WhatsApp/Telegram feel natural,
not like a dump of five messages at once.
"""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable

from wax.observability.logging import get_logger

logger = get_logger(__name__)

SendFn = Callable[[str], Awaitable[dict[str, Any]]]


async def send_paced(
    chunks: list[str],
    send_one: SendFn,
    *,
    delay_seconds: float = 0.6,
) -> list[dict[str, Any]]:
    results = []
    for i, chunk in enumerate(chunks):
        results.append(await send_one(chunk))
        if i < len(chunks) - 1 and delay_seconds > 0:
            await asyncio.sleep(delay_seconds)
    return results
