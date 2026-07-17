from __future__ import annotations

import logging
from time import perf_counter
from uuid import uuid4

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

logger = logging.getLogger(__name__)


class TraceLoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):  # type: ignore[no-untyped-def]
        trace_id = request.headers.get("X-Trace-ID") or uuid4().hex
        request.state.trace_id = trace_id
        started = perf_counter()
        response = await call_next(request)
        response.headers["X-Trace-ID"] = trace_id
        logger.info(
            "http_request trace_id=%s method=%s path=%s status=%s total_latency_ms=%.2f",
            trace_id,
            request.method,
            request.url.path,
            response.status_code,
            (perf_counter() - started) * 1000,
        )
        return response
