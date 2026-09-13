"""HTTP middleware for request correlation and execution timing."""

import logging
import time
import uuid

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger(__name__)


class RequestIDMiddleware(BaseHTTPMiddleware):
    """Attach inbound or generated X-Request-ID headers and log timings."""

    async def dispatch(self, request: Request, call_next):
        # Preserve inbound trace identifier or generate a compact token.
        request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:12]
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            logger.exception(f"[{request_id}] {request.method} {request.url.path} failed")
            raise
        elapsed_ms = (time.perf_counter() - started) * 1000
        # Propagate request ID to client for log correlation.
        response.headers["X-Request-ID"] = request_id
        logger.info(
            f"[{request_id}] {request.method} {request.url.path} {response.status_code} {elapsed_ms:.1f}ms"
        )
        return response
