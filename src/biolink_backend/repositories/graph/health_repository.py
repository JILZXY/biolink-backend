from __future__ import annotations

import time

from biolink_backend.application.ports import EngineHealthStatus
from biolink_backend.infrastructure.neo4j import Neo4jConnectionProvider


class Neo4jHealthRepository:
    """Sonda Bolt nativa para Neo4j Community 2026.10.2."""

    def __init__(self, provider: Neo4jConnectionProvider) -> None:
        self._provider = provider

    def check_health(self) -> EngineHealthStatus:
        start_ns = time.perf_counter_ns()
        try:
            with self._provider.session() as session:
                ping_rec = session.run("RETURN 1 AS ok;").single()
                if not ping_rec or ping_rec.get("ok") != 1:
                    raise RuntimeError("Respuesta inesperada del ping Bolt de Neo4j.")
                comp_rows = session.run(
                    "CALL dbms.components() YIELD name, versions RETURN name, versions;"
                ).data()
            elapsed_ms = round((time.perf_counter_ns() - start_ns) / 1_000_000, 3)
            version = None
            for row in comp_rows:
                if row.get("name") == "Neo4j Kernel" and row.get("versions"):
                    version = str(row["versions"][0])
                    break
            return EngineHealthStatus(
                engine="neo4j",
                healthy=True,
                latency_ms=elapsed_ms,
                version=version,
            )
        except Exception as exc:  # noqa: BLE001
            elapsed_ms = round((time.perf_counter_ns() - start_ns) / 1_000_000, 3)
            return EngineHealthStatus(
                engine="neo4j",
                healthy=False,
                latency_ms=elapsed_ms,
                error=str(exc),
            )
