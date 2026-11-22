from __future__ import annotations

import psycopg

from biolink_backend.application.dto import OutboxEventDTO
from biolink_backend.domain.entities import OutboxEvent
from biolink_backend.domain.vocabularies import OutboxEventType, OutboxStatus
from biolink_backend.infrastructure.postgresql import PostgresConnectionProvider
from biolink_backend.shared.exceptions import DatabaseUnavailableError


class PostgresOutboxRepository:
    """Repositorio SQL nativo para el Transactional Outbox Pattern en PostgreSQL 18.7."""

    def __init__(self, provider: PostgresConnectionProvider) -> None:
        self._provider = provider

    def claim_pending_events(self, limit: int = 50, *, max_attempts: int = 5) -> list[OutboxEvent]:
        """Reclama un lote pendiente usando FOR UPDATE SKIP LOCKED para evitar contencion."""
        sql = """
        WITH next_batch AS (
            SELECT outbox_event_id
            FROM outbox_event
            WHERE status IN ('PENDING', 'FAILED')
              AND attempts < %s
            ORDER BY created_at ASC
            LIMIT %s
            FOR UPDATE SKIP LOCKED
        )
        UPDATE outbox_event o
        SET status = 'PROCESSING'
        FROM next_batch nb
        WHERE o.outbox_event_id = nb.outbox_event_id
        RETURNING
            o.outbox_event_id,
            o.aggregate_type,
            o.aggregate_id,
            o.event_type,
            o.aggregate_version,
            o.payload_version,
            o.payload_json,
            o.status,
            o.attempts,
            o.created_at,
            o.processed_at,
            o.error_message;
        """
        try:
            with self._provider.connection() as conn:
                with conn.transaction():
                    with conn.cursor() as cur:
                        cur.execute(sql, (max_attempts, limit))
                        rows = cur.fetchall()
        except psycopg.Error as exc:
            raise DatabaseUnavailableError("postgresql", str(exc)) from exc

        return [
            OutboxEvent(
                outbox_event_id=row["outbox_event_id"],
                aggregate_type=row["aggregate_type"],
                aggregate_id=row["aggregate_id"],
                event_type=OutboxEventType(row["event_type"]),
                aggregate_version=int(row.get("aggregate_version", 1)),
                payload_version=int(row.get("payload_version", 1)),
                payload_json=dict(row["payload_json"]),
                status=OutboxStatus(row["status"]),
                attempts=int(row["attempts"]),
                created_at=row["created_at"],
                processed_at=row["processed_at"],
                error_message=row["error_message"],
            )
            for row in rows
        ]

    def mark_processed(self, outbox_event_id: str) -> None:
        sql_outbox = """
        UPDATE outbox_event
        SET status = 'PROCESSED',
            processed_at = clock_timestamp(),
            error_message = NULL
        WHERE outbox_event_id = %s
        RETURNING aggregate_type, aggregate_id, aggregate_version;
        """
        sql_assertion = """
        UPDATE interaction_assertion
        SET projection_status = 'SYNCED',
            last_projected_at = clock_timestamp()
        WHERE assertion_id = %s
          AND aggregate_version <= %s;
        """
        try:
            with self._provider.connection() as conn:
                with conn.transaction():
                    with conn.cursor() as cur:
                        cur.execute(sql_outbox, (outbox_event_id,))
                        row = cur.fetchone()
                        if row and row["aggregate_type"] == "InteractionAssertion":
                            cur.execute(
                                sql_assertion,
                                (row["aggregate_id"], int(row["aggregate_version"])),
                            )
        except psycopg.Error as exc:
            raise DatabaseUnavailableError("postgresql", str(exc)) from exc

    def mark_failed(
        self,
        outbox_event_id: str,
        *,
        error_message: str,
        max_attempts: int = 5,
    ) -> None:
        sql_outbox = """
        UPDATE outbox_event
        SET attempts = attempts + 1,
            error_message = %s,
            status = CASE
                WHEN attempts + 1 >= %s THEN 'DEAD_LETTER'
                ELSE 'FAILED'
            END
        WHERE outbox_event_id = %s
        RETURNING aggregate_type, aggregate_id, aggregate_version;
        """
        sql_assertion = """
        UPDATE interaction_assertion
        SET projection_status = 'FAILED'
        WHERE assertion_id = %s
          AND aggregate_version <= %s;
        """
        truncated_err = error_message[:2000]
        try:
            with self._provider.connection() as conn:
                with conn.transaction():
                    with conn.cursor() as cur:
                        cur.execute(sql_outbox, (truncated_err, max_attempts, outbox_event_id))
                        row = cur.fetchone()
                        if row and row["aggregate_type"] == "InteractionAssertion":
                            cur.execute(
                                sql_assertion,
                                (row["aggregate_id"], int(row["aggregate_version"])),
                            )
        except psycopg.Error as exc:
            raise DatabaseUnavailableError("postgresql", str(exc)) from exc

    def reset_failed_and_dead_letter_events(self, assertion_id: str | None = None) -> int:
        """Reencola eventos FAILED, DEAD_LETTER o PROCESSING atascados para reconciliacion."""
        sql = """
        UPDATE outbox_event
        SET status = 'PENDING',
            attempts = 0,
            error_message = NULL
        WHERE status IN ('FAILED', 'DEAD_LETTER', 'PROCESSING')
          AND (%s::varchar IS NULL OR aggregate_id = %s);
        """
        try:
            with self._provider.connection() as conn:
                with conn.transaction():
                    with conn.cursor() as cur:
                        cur.execute(sql, (assertion_id, assertion_id))
                        return int(cur.rowcount)
        except psycopg.Error as exc:
            raise DatabaseUnavailableError("postgresql", str(exc)) from exc

    def list_events_for_aggregate(self, aggregate_id: str) -> list[OutboxEventDTO]:
        sql = """
        SELECT
            outbox_event_id,
            event_type,
            aggregate_id,
            aggregate_version,
            payload_version,
            created_at,
            status,
            attempts,
            error_message,
            processed_at,
            payload_json
        FROM outbox_event
        WHERE aggregate_id = %s
        ORDER BY created_at ASC, outbox_event_id ASC;
        """
        try:
            with self._provider.connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(sql, (aggregate_id,))
                    rows = cur.fetchall()
        except psycopg.Error as exc:
            raise DatabaseUnavailableError("postgresql", str(exc)) from exc

        return [
            OutboxEventDTO(
                event_id=row["outbox_event_id"],
                event_type=row["event_type"],
                assertion_id=row["aggregate_id"],
                aggregate_version=int(row["aggregate_version"]),
                payload_version=int(row["payload_version"]),
                created_at=row["created_at"].isoformat(),
                processing_status=row["status"],
                attempts=int(row["attempts"]),
                last_error=row["error_message"],
                processed_at=row["processed_at"].isoformat() if row["processed_at"] else None,
                payload_json=dict(row["payload_json"]),
            )
            for row in rows
        ]
