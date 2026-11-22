from __future__ import annotations

import time

from biolink_backend.application.ports import EngineHealthStatus
from biolink_backend.infrastructure.postgresql import PostgresConnectionProvider


class PostgresHealthRepository:
    """Sonda SQL directa de conectividad y disponibilidad de PostgreSQL 18.7."""

    def __init__(self, provider: PostgresConnectionProvider) -> None:
        self._provider = provider

    def check_health(self) -> EngineHealthStatus:
        start_ns = time.perf_counter_ns()
        try:
            with self._provider.connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT 1 AS ok, current_setting('server_version') AS version;")
                    row = cur.fetchone()
            elapsed_ms = round((time.perf_counter_ns() - start_ns) / 1_000_000, 3)
            version = str(row["version"]) if row else None
            return EngineHealthStatus(
                engine="postgresql",
                healthy=True,
                latency_ms=elapsed_ms,
                version=version,
            )
        except Exception as exc:  # noqa: BLE001
            elapsed_ms = round((time.perf_counter_ns() - start_ns) / 1_000_000, 3)
            return EngineHealthStatus(
                engine="postgresql",
                healthy=False,
                latency_ms=elapsed_ms,
                error=str(exc),
            )
