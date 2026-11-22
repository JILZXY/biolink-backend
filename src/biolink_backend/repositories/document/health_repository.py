from __future__ import annotations

import time

from biolink_backend.application.ports import EngineHealthStatus
from biolink_backend.infrastructure.mongodb import MongoConnectionProvider


class MongoHealthRepository:
    """Sonda nativa PyMongo de disponibilidad para MongoDB Community 8.0.38."""

    def __init__(self, provider: MongoConnectionProvider) -> None:
        self._provider = provider

    def check_health(self) -> EngineHealthStatus:
        start_ns = time.perf_counter_ns()
        try:
            db = self._provider.get_database()
            db.command("ping")
            info = self._provider.client.server_info()
            elapsed_ms = round((time.perf_counter_ns() - start_ns) / 1_000_000, 3)
            version = str(info.get("version")) if isinstance(info, dict) else None
            return EngineHealthStatus(
                engine="mongodb",
                healthy=True,
                latency_ms=elapsed_ms,
                version=version,
            )
        except Exception as exc:  # noqa: BLE001
            elapsed_ms = round((time.perf_counter_ns() - start_ns) / 1_000_000, 3)
            return EngineHealthStatus(
                engine="mongodb",
                healthy=False,
                latency_ms=elapsed_ms,
                error=str(exc),
            )
