from __future__ import annotations

import json
import logging
import sys
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any

request_id_ctx: ContextVar[str | None] = ContextVar("request_id", default=None)

_STANDARD_ATTRS = frozenset({
    "args",
    "asctime",
    "created",
    "exc_info",
    "exc_text",
    "filename",
    "funcName",
    "levelname",
    "levelno",
    "lineno",
    "module",
    "msecs",
    "message",
    "msg",
    "name",
    "pathname",
    "process",
    "processName",
    "relativeCreated",
    "stack_info",
    "thread",
    "threadName",
    "taskName",
})

_SENSITIVE_KEYS = frozenset({"password", "password_hash", "token", "secret", "authorization", "cookie"})


def _redact_mapping(data: dict[str, Any]) -> dict[str, Any]:
    cleaned: dict[str, Any] = {}
    for key, val in data.items():
        lower_k = key.lower()
        if any(s in lower_k for s in _SENSITIVE_KEYS):
            cleaned[key] = "***REDACTED***"
        elif isinstance(val, dict):
            cleaned[key] = _redact_mapping(val)
        else:
            cleaned[key] = val
    return cleaned


class StructuredJsonFormatter(logging.Formatter):
    """Formateador JSON estructurado con trazabilidad de request_id y redaccion de secretos."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        req_id = request_id_ctx.get()
        if req_id:
            payload["request_id"] = req_id

        extra_fields = {
            k: v for k, v in record.__dict__.items() if k not in _STANDARD_ATTRS and not k.startswith("_")
        }
        if extra_fields:
            payload["context"] = _redact_mapping(extra_fields)

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging(level: str = "INFO", log_format: str = "json") -> None:
    """Configura el sistema de logging raiz del backend."""
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    handler = logging.StreamHandler(sys.stdout)
    if log_format == "json":
        handler.setFormatter(StructuredJsonFormatter())
    else:
        handler.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
        )
    root.addHandler(handler)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
