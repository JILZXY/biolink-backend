from __future__ import annotations

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from biolink_backend.shared.exceptions import BioLinkError
from biolink_backend.shared.logging import get_logger, request_id_ctx

logger = get_logger(__name__)


def _build_error_payload(
    *,
    code: str,
    message: str,
    details: dict | list | None = None,
) -> dict:
    return {
        "error": {
            "code": code,
            "message": message,
            "details": details or {},
            "request_id": request_id_ctx.get(),
        }
    }


def register_exception_handlers(app: FastAPI) -> None:
    """Registra manejadores uniformes para errores de dominio, validacion y fallos internos."""

    @app.exception_handler(BioLinkError)
    async def handle_biolink_error(_request: Request, exc: BioLinkError) -> JSONResponse:
        log_fn = logger.warning if exc.http_status < 500 else logger.error
        log_fn(
            "Error controlado en BioLink",
            extra={"code": exc.code, "http_status": exc.http_status, "details": exc.details},
        )
        return JSONResponse(
            status_code=exc.http_status,
            content=_build_error_payload(code=exc.code, message=exc.message, details=exc.details),
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(_request: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            content=_build_error_payload(
                code="REQUEST_VALIDATION_ERROR",
                message="La solicitud contiene parametros invalidos o incompletos.",
                details={"validation_errors": exc.errors()},
            ),
        )

    @app.exception_handler(Exception)
    async def handle_unexpected_error(_request: Request, exc: Exception) -> JSONResponse:
        logger.exception("Error interno no controlado", extra={"exception_type": type(exc).__name__})
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=_build_error_payload(
                code="INTERNAL_SERVER_ERROR",
                message="Ocurrio un error interno inesperado al procesar la solicitud.",
                details={},
            ),
        )
