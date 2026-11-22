from __future__ import annotations

import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from biolink_backend.shared.logging import get_logger, request_id_ctx

logger = get_logger(__name__)


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Propaga X-Request-ID, mide latencia y agrega cabeceras basicas de seguridad HTTP."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        incoming_id = request.headers.get("X-Request-ID", "").strip()
        req_id = incoming_id or uuid.uuid4().hex
        token = request_id_ctx.set(req_id)
        start_ns = time.perf_counter_ns()
        try:
            response = await call_next(request)
        finally:
            elapsed_ms = round((time.perf_counter_ns() - start_ns) / 1_000_000, 3)
            request_id_ctx.reset(token)

        response.headers["X-Request-ID"] = req_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Cache-Control"] = "no-store"

        logger.info(
            "Solicitud HTTP procesada",
            extra={
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
                "latency_ms": elapsed_ms,
                "request_id": req_id,
            },
        )
        return response
