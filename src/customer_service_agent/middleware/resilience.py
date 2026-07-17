from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import TypeVar

from customer_service_agent.exceptions import BackendError

T = TypeVar("T")


async def retry_query(
    operation: Callable[[], Awaitable[T]],
    *,
    max_attempts: int,
    base_delay: float = 0.05,
) -> T:
    """Retry only explicitly retryable read operations."""

    for attempt in range(1, max_attempts + 1):
        try:
            return await operation()
        except BackendError as exc:
            if not exc.retryable or attempt == max_attempts:
                raise
            await asyncio.sleep(base_delay * (2 ** (attempt - 1)))
    raise RuntimeError("unreachable")
