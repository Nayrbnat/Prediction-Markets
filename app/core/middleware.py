"""Correlation-id middleware: stamps each request with an id, logs in/out with
latency, and echoes the id back in the ``x-request-id`` response header.
"""

from __future__ import annotations

import time
from uuid import uuid4

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.core.logging import correlation_id, get_logger

logger = get_logger(__name__)


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        rid = request.headers.get("x-request-id") or uuid4().hex
        token = correlation_id.set(rid)
        start = time.perf_counter()
        logger.info("request.in", extra={"method": request.method, "path": request.url.path})
        try:
            response = await call_next(request)
        finally:
            elapsed_ms = round((time.perf_counter() - start) * 1000, 1)
        logger.info(
            "request.out",
            extra={
                "method": request.method,
                "path": request.url.path,
                "status": response.status_code,
                "latency_ms": elapsed_ms,
            },
        )
        response.headers["x-request-id"] = rid
        correlation_id.reset(token)
        return response
