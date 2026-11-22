from __future__ import annotations

import signal
import threading

from biolink_backend.application.ports import (
    AdminRelationalPort,
    GraphProjectionPort,
    OutboxRepositoryPort,
)
from biolink_backend.config.settings import Settings, get_settings
from biolink_backend.domain.entities import OutboxEvent
from biolink_backend.domain.vocabularies import OutboxEventType
from biolink_backend.infrastructure.manager import DatabaseConnectionManager
from biolink_backend.repositories.graph.projection_repository import Neo4jProjectionRepository
from biolink_backend.repositories.relational.outbox_repository import PostgresOutboxRepository
from biolink_backend.shared.exceptions import BioLinkError
from biolink_backend.shared.logging import configure_logging, get_logger

logger = get_logger(__name__)


class OutboxProjectionWorker:
    """Worker interno idempotente que proyecta eventos confirmados de PostgreSQL hacia Neo4j."""

    def __init__(
        self,
        *,
        outbox_repo: OutboxRepositoryPort,
        graph_repo: GraphProjectionPort,
        batch_size: int = 50,
        max_attempts: int = 5,
        poll_interval_sec: float = 2.0,
    ) -> None:
        self._outbox_repo = outbox_repo
        self._graph_repo = graph_repo
        self._batch_size = batch_size
        self._max_attempts = max_attempts
        self._poll_interval_sec = poll_interval_sec

    def _apply_event_to_graph(self, event: OutboxEvent) -> None:
        payload = dict(event.payload_json)
        payload.setdefault("aggregate_version", event.aggregate_version)
        if event.event_type in (
            OutboxEventType.ASSERTION_PUBLISHED,
            OutboxEventType.ASSERTION_REVISED,
        ):
            self._graph_repo.project_assertion(payload)
        elif event.event_type == OutboxEventType.ASSERTION_WITHDRAWN:
            self._graph_repo.withdraw_assertion(event.aggregate_id)
        else:
            raise ValueError(f"Tipo de evento outbox no soportado: '{event.event_type}'.")

    def _process_batch(self) -> tuple[int, int]:
        """Reclama y procesa un lote del Transactional Outbox. Devuelve (procesados, fallidos)."""
        events = self._outbox_repo.claim_pending_events(
            self._batch_size,
            max_attempts=self._max_attempts,
        )
        if not events:
            return 0, 0

        processed = 0
        failed = 0
        # Deduplicacion intra-lote por (aggregate_id, aggregate_version, event_type)
        seen_keys: set[tuple[str, int, OutboxEventType]] = set()
        for event in events:
            dedup_key = (event.aggregate_id, event.aggregate_version, event.event_type)
            try:
                if dedup_key not in seen_keys:
                    self._apply_event_to_graph(event)
                    seen_keys.add(dedup_key)

                self._outbox_repo.mark_processed(event.outbox_event_id)
                processed += 1
                logger.info(
                    "Evento outbox proyectado en Neo4j",
                    extra={
                        "outbox_event_id": event.outbox_event_id,
                        "aggregate_id": event.aggregate_id,
                        "aggregate_version": event.aggregate_version,
                        "event_type": event.event_type.value,
                    },
                )
            except Exception as exc:  # noqa: BLE001
                failed += 1
                err_msg = str(exc)
                if isinstance(exc, BioLinkError) and exc.details:
                    err_msg = f"{err_msg} | details={exc.details}"
                self._outbox_repo.mark_failed(
                    event.outbox_event_id,
                    error_message=err_msg,
                    max_attempts=self._max_attempts,
                )
                logger.error(
                    "Fallo procesando evento outbox",
                    extra={
                        "outbox_event_id": event.outbox_event_id,
                        "aggregate_id": event.aggregate_id,
                        "attempts": event.attempts + 1,
                        "error": err_msg,
                    },
                )
        return processed, failed

    def process_once(self) -> int:
        """Reclama y procesa un lote del Transactional Outbox. Devuelve cantidad procesada."""
        processed, _failed = self._process_batch()
        return processed

    def process_once_detailed(self) -> tuple[int, int]:
        """Reclama y procesa un lote devolviendo (procesados, fallidos)."""
        return self._process_batch()

    def drain_once(self, *, max_batches: int = 20) -> tuple[int, int]:
        """Drena multiples lotes hasta agotar eventos pendientes o encontrar fallos."""
        total_processed = 0
        total_failed = 0
        for _ in range(max_batches):
            processed, failed = self._process_batch()
            total_processed += processed
            total_failed += failed
            if (processed == 0 and failed == 0) or failed > 0:
                break
        return total_processed, total_failed

    def reconcile_from_source_of_truth(
        self, admin_relational: AdminRelationalPort
    ) -> dict[str, int]:
        """Reconciliacion completa de Neo4j desde la fuente de verdad PostgreSQL tras caidas."""
        published_payloads = admin_relational.list_all_published_assertion_payloads()
        stats = self._graph_repo.reconcile_published_assertions(published_payloads)
        published_ids = [str(item["assertion_id"]) for item in published_payloads]
        if published_ids:
            admin_relational.mark_assertions_projection_synced(published_ids)
        return stats

    def run_forever(self, stop_event: threading.Event | None = None) -> None:
        """Bucle continuo controlado con parada limpia por senal y backoff ante fallos."""
        stopper = stop_event or threading.Event()
        logger.info(
            "OutboxProjectionWorker iniciado",
            extra={
                "poll_interval_sec": self._poll_interval_sec,
                "batch_size": self._batch_size,
                "max_attempts": self._max_attempts,
            },
        )
        consecutive_failures = 0
        while not stopper.is_set():
            try:
                processed, failed = self._process_batch()
                if failed > 0:
                    consecutive_failures += 1
                    backoff = min(self._poll_interval_sec * (2 ** (consecutive_failures - 1)), 30.0)
                    stopper.wait(timeout=backoff)
                elif processed == 0:
                    consecutive_failures = 0
                    stopper.wait(timeout=self._poll_interval_sec)
                else:
                    consecutive_failures = 0
            except Exception as exc:  # noqa: BLE001
                consecutive_failures += 1
                logger.error("Error de ciclo en worker outbox", extra={"error": str(exc)})
                backoff = min(self._poll_interval_sec * (2 ** (consecutive_failures - 1)), 30.0)
                stopper.wait(timeout=backoff)
        logger.info("OutboxProjectionWorker detenido limpiamente")


def main() -> None:
    settings: Settings = get_settings()
    configure_logging(settings.log_level, settings.log_format)
    manager = DatabaseConnectionManager(settings)
    manager.startup()

    stop_event = threading.Event()

    def _handle_signal(_signum: int, _frame: object) -> None:
        stop_event.set()

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    worker = OutboxProjectionWorker(
        outbox_repo=PostgresOutboxRepository(manager.postgres),
        graph_repo=Neo4jProjectionRepository(manager.neo4j),
        batch_size=settings.outbox_batch_size,
        max_attempts=settings.outbox_max_attempts,
        poll_interval_sec=settings.outbox_poll_interval_sec,
    )
    try:
        worker.run_forever(stop_event)
    finally:
        manager.shutdown()


if __name__ == "__main__":
    main()
