from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime

from biolink_backend.application.ports import (
    DocumentHealthPort,
    EngineHealthStatus,
    GraphHealthPort,
    RelationalHealthPort,
)
from biolink_backend.config.settings import Settings
from biolink_backend.shared.time import utc_now


@dataclass(frozen=True, slots=True)
class LivenessReport:
    status: str
    service: str
    version: str
    environment: str
    uptime_seconds: float
    timestamp_utc: datetime


@dataclass(frozen=True, slots=True)
class ReadinessReport:
    status: str
    ready: bool
    timestamp_utc: datetime
    dependencies: dict[str, EngineHealthStatus]


@dataclass(frozen=True, slots=True)
class ConsolidatedHealthReport:
    status: str
    live: bool
    ready: bool
    service: str
    version: str
    environment: str
    uptime_seconds: float
    timestamp_utc: datetime
    memory_budget_mb: dict[str, int]
    dependencies: dict[str, EngineHealthStatus]


class HealthService:
    """Servicio de aplicacion que separa liveness de readiness real sobre los 3 motores."""

    def __init__(
        self,
        *,
        settings: Settings,
        relational: RelationalHealthPort,
        document: DocumentHealthPort,
        graph: GraphHealthPort,
        started_monotonic: float | None = None,
    ) -> None:
        self._settings = settings
        self._relational = relational
        self._document = document
        self._graph = graph
        self._started_monotonic = started_monotonic if started_monotonic is not None else time.monotonic()

    def check_liveness(self) -> LivenessReport:
        """Verifica exclusivamente que el proceso Python/FastAPI siga vivo sin tocar BDs."""
        uptime = round(max(0.0, time.monotonic() - self._started_monotonic), 3)
        return LivenessReport(
            status="alive",
            service=self._settings.app_name,
            version=self._settings.app_version,
            environment=self._settings.app_env,
            uptime_seconds=uptime,
            timestamp_utc=utc_now(),
        )

    def check_readiness(self) -> ReadinessReport:
        """Comprueba disponibilidad real de PostgreSQL, MongoDB y Neo4j."""
        pg_status = self._relational.check_health()
        mongo_status = self._document.check_health()
        neo4j_status = self._graph.check_health()
        deps = {
            "postgresql": pg_status,
            "mongodb": mongo_status,
            "neo4j": neo4j_status,
        }
        all_ready = all(item.healthy for item in deps.values())
        return ReadinessReport(
            status="ready" if all_ready else "not_ready",
            ready=all_ready,
            timestamp_utc=utc_now(),
            dependencies=deps,
        )

    def check_consolidated(self) -> ConsolidatedHealthReport:
        """Retorna el estado combinado de proceso, presupuesto y dependencias."""
        live = self.check_liveness()
        readiness = self.check_readiness()
        overall_status = "ok" if readiness.ready else "degraded"
        return ConsolidatedHealthReport(
            status=overall_status,
            live=True,
            ready=readiness.ready,
            service=live.service,
            version=live.version,
            environment=live.environment,
            uptime_seconds=live.uptime_seconds,
            timestamp_utc=readiness.timestamp_utc,
            memory_budget_mb=self._settings.memory_budget_summary(),
            dependencies=readiness.dependencies,
        )
