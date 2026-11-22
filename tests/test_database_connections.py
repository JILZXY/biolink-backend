from __future__ import annotations

import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any

import pytest

from biolink_backend.config.settings import Settings
from biolink_backend.domain.entities import OutboxEvent
from biolink_backend.domain.vocabularies import OutboxEventType, OutboxStatus
from biolink_backend.infrastructure.manager import DatabaseConnectionManager
from biolink_backend.repositories.document.health_repository import MongoHealthRepository
from biolink_backend.repositories.graph.health_repository import Neo4jHealthRepository
from biolink_backend.repositories.graph.projection_repository import (
    _WITHDRAW_BATCH_CYPHER,
    Neo4jProjectionRepository,
)
from biolink_backend.repositories.relational.health_repository import PostgresHealthRepository
from biolink_backend.shared.exceptions import BusinessRuleViolationError
from biolink_backend.workers.outbox_worker import OutboxProjectionWorker


def test_connection_manager_startup_is_lazy(test_settings: Settings) -> None:
    manager = DatabaseConnectionManager(test_settings)
    manager.startup()
    try:
        # La inicializacion perezosa no abre sockets ni hilos en segundo plano durante el arranque
        assert manager.postgres._pool is None
        assert manager.mongodb._client is None
        assert manager.neo4j._driver is None
    finally:
        manager.shutdown()


def test_health_repositories_handle_healthy_and_failing_providers() -> None:
    class _FailingPostgresProvider:
        @contextmanager
        def connection(self):
            raise ConnectionError("Postgres socket refused")
            yield  # pragma: no cover

    class _FailingMongoProvider:
        def get_database(self):
            raise ConnectionError("Mongo selection timeout")

    class _FailingNeo4jProvider:
        @contextmanager
        def session(self, **_kw):
            raise ConnectionError("Neo4j bolt unreachable")
            yield  # pragma: no cover

    pg_fail = PostgresHealthRepository(_FailingPostgresProvider()).check_health()  # type: ignore[arg-type]
    mongo_fail = MongoHealthRepository(_FailingMongoProvider()).check_health()  # type: ignore[arg-type]
    neo_fail = Neo4jHealthRepository(_FailingNeo4jProvider()).check_health()  # type: ignore[arg-type]

    assert pg_fail.healthy is False and pg_fail.engine == "postgresql" and "refused" in (pg_fail.error or "")
    assert mongo_fail.healthy is False and mongo_fail.engine == "mongodb" and "timeout" in (mongo_fail.error or "")
    assert neo_fail.healthy is False and neo_fail.engine == "neo4j" and "unreachable" in (neo_fail.error or "")

    class _HealthyCursor:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, _sql: str) -> None:
            return None

        def fetchone(self) -> dict[str, Any]:
            return {"ok": 1, "version": "18.7"}

    class _HealthyPgConn:
        def cursor(self):
            return _HealthyCursor()

    class _HealthyPostgresProvider:
        @contextmanager
        def connection(self):
            yield _HealthyPgConn()

    class _HealthyMongoDb:
        def command(self, _cmd: str) -> dict[str, Any]:
            return {"ok": 1}

    class _HealthyMongoClient:
        def server_info(self) -> dict[str, Any]:
            return {"version": "8.0.38"}

    class _HealthyMongoProvider:
        client = _HealthyMongoClient()

        def get_database(self):
            return _HealthyMongoDb()

    class _HealthyNeo4jResult:
        def __init__(self, row=None, rows=None) -> None:
            self._row = row
            self._rows = rows or []

        def single(self):
            return self._row

        def data(self):
            return self._rows

    class _HealthyNeo4jSession:
        def run(self, query: str):
            if "RETURN 1" in query:
                return _HealthyNeo4jResult(row={"ok": 1})
            return _HealthyNeo4jResult(rows=[{"name": "Neo4j Kernel", "versions": ["2026.10.2"]}])

    class _HealthyNeo4jProvider:
        @contextmanager
        def session(self, **_kw):
            yield _HealthyNeo4jSession()

    assert PostgresHealthRepository(_HealthyPostgresProvider()).check_health().version == "18.7"  # type: ignore[arg-type]
    assert MongoHealthRepository(_HealthyMongoProvider()).check_health().version == "8.0.38"  # type: ignore[arg-type]
    assert Neo4jHealthRepository(_HealthyNeo4jProvider()).check_health().version == "2026.10.2"  # type: ignore[arg-type]


def test_neo4j_projection_repository_rules_and_batch_cypher(test_settings: Settings) -> None:
    manager = DatabaseConnectionManager(test_settings)
    repo = Neo4jProjectionRepository(manager.neo4j)

    # Regla RD-16: rechazar estados no PUBLISHED antes de tocar Bolt
    with pytest.raises(BusinessRuleViolationError):
        repo.project_assertion({"assertion_id": "AST_1", "status": "DRAFT", "evidence_ids": ["EVD_1"]})

    # Regla RD-02: rechazar afirmacion sin evidencia
    with pytest.raises(BusinessRuleViolationError):
        repo.project_assertion({"assertion_id": "AST_1", "status": "PUBLISHED", "evidence_ids": []})

    # Regla ADR-004: retiro por lotes segmentado en transacciones de 1000 filas
    assert "IN TRANSACTIONS OF 1000 ROWS" in _WITHDRAW_BATCH_CYPHER


class FakeOutboxRepo:
    def __init__(self, events: list[OutboxEvent]) -> None:
        self.events = events
        self.processed: list[str] = []
        self.failed: list[tuple[str, str, int]] = []

    def claim_pending_events(self, limit: int, *, max_attempts: int = 5) -> list[OutboxEvent]:
        return self.events[:limit]

    def mark_processed(self, outbox_event_id: str) -> None:
        self.processed.append(outbox_event_id)

    def mark_failed(self, outbox_event_id: str, *, error_message: str, max_attempts: int) -> None:
        self.failed.append((outbox_event_id, error_message, max_attempts))


class FakeGraphProjectionRepo:
    def __init__(self, *, fail_id: str | None = None) -> None:
        self.fail_id = fail_id
        self.projected: list[str] = []
        self.withdrawn: list[str] = []

    def project_assertion(self, payload: dict[str, Any]) -> str:
        aid = payload["assertion_id"]
        if aid == self.fail_id:
            raise RuntimeError("Neo4j transient bolt failure")
        self.projected.append(aid)
        return aid

    def withdraw_assertion(self, assertion_id: str) -> int:
        self.withdrawn.append(assertion_id)
        return 1

    def withdraw_assertions_batch(self, assertion_ids: list[str]) -> int:
        self.withdrawn.extend(assertion_ids)
        return len(assertion_ids)


def test_outbox_worker_processes_publish_withdraw_and_failures() -> None:
    now = datetime.now(timezone.utc)
    events = [
        OutboxEvent(
            outbox_event_id="OBX_1",
            aggregate_type="InteractionAssertion",
            aggregate_id="AST_1",
            event_type=OutboxEventType.ASSERTION_PUBLISHED,
            payload_json={"assertion_id": "AST_1", "status": "PUBLISHED", "evidence_ids": ["EVD_1"]},
            status=OutboxStatus.PROCESSING,
            attempts=0,
            created_at=now,
        ),
        OutboxEvent(
            outbox_event_id="OBX_2",
            aggregate_type="InteractionAssertion",
            aggregate_id="AST_2",
            event_type=OutboxEventType.ASSERTION_WITHDRAWN,
            payload_json={"assertion_id": "AST_2"},
            status=OutboxStatus.PROCESSING,
            attempts=0,
            created_at=now,
        ),
        OutboxEvent(
            outbox_event_id="OBX_3",
            aggregate_type="InteractionAssertion",
            aggregate_id="AST_FAIL",
            event_type=OutboxEventType.ASSERTION_PUBLISHED,
            payload_json={"assertion_id": "AST_FAIL", "status": "PUBLISHED", "evidence_ids": ["EVD_3"]},
            status=OutboxStatus.PROCESSING,
            attempts=1,
            created_at=now,
        ),
    ]
    outbox_repo = FakeOutboxRepo(events)
    graph_repo = FakeGraphProjectionRepo(fail_id="AST_FAIL")

    worker = OutboxProjectionWorker(
        outbox_repo=outbox_repo,
        graph_repo=graph_repo,
        batch_size=10,
        max_attempts=5,
        poll_interval_sec=0.2,
    )
    count = worker.process_once()

    assert count == 2
    assert graph_repo.projected == ["AST_1"]
    assert graph_repo.withdrawn == ["AST_2"]
    assert outbox_repo.processed == ["OBX_1", "OBX_2"]
    assert len(outbox_repo.failed) == 1
    assert outbox_repo.failed[0][0] == "OBX_3"


def test_outbox_worker_run_forever_waits_after_partial_failures() -> None:
    now = datetime.now(timezone.utc)
    events = [
        OutboxEvent(
            outbox_event_id="OBX_OK",
            aggregate_type="InteractionAssertion",
            aggregate_id="AST_OK",
            event_type=OutboxEventType.ASSERTION_PUBLISHED,
            payload_json={"assertion_id": "AST_OK", "status": "PUBLISHED", "evidence_ids": ["EVD_1"]},
            status=OutboxStatus.PROCESSING,
            attempts=0,
            created_at=now,
        ),
        OutboxEvent(
            outbox_event_id="OBX_ERR",
            aggregate_type="InteractionAssertion",
            aggregate_id="AST_ERR",
            event_type=OutboxEventType.ASSERTION_PUBLISHED,
            payload_json={"assertion_id": "AST_ERR", "status": "PUBLISHED", "evidence_ids": ["EVD_2"]},
            status=OutboxStatus.PROCESSING,
            attempts=0,
            created_at=now,
        ),
    ]
    outbox_repo = FakeOutboxRepo(events)
    graph_repo = FakeGraphProjectionRepo(fail_id="AST_ERR")
    worker = OutboxProjectionWorker(
        outbox_repo=outbox_repo,
        graph_repo=graph_repo,
        batch_size=10,
        max_attempts=5,
        poll_interval_sec=0.5,
    )

    class _StopAfterFirstWait(threading.Event):
        def __init__(self) -> None:
            super().__init__()
            self.wait_timeouts: list[float | None] = []

        def wait(self, timeout: float | None = None) -> bool:
            self.wait_timeouts.append(timeout)
            self.set()
            return True

    stopper = _StopAfterFirstWait()
    worker.run_forever(stopper)
    assert stopper.wait_timeouts == [0.5]
    assert len(outbox_repo.processed) == 1
    assert len(outbox_repo.failed) == 1
