from __future__ import annotations

from typing import Any

from neo4j.exceptions import DriverError, Neo4jError

from biolink_backend.infrastructure.neo4j import Neo4jConnectionProvider
from biolink_backend.shared.exceptions import BusinessRuleViolationError, DatabaseUnavailableError

_PROJECT_ASSERTION_CYPHER = """
WITH $status AS st
WHERE st = 'PUBLISHED'

MERGE (actor:Taxon {taxon_id: $actor_taxon_id})
ON CREATE SET
  actor.accepted_name = coalesce($actor_name, 'Unknown Taxon'),
  actor.organism_group = 'ANIMAL',
  actor.projected_at = datetime()

MERGE (target:Taxon {taxon_id: $target_taxon_id})
ON CREATE SET
  target.accepted_name = coalesce($target_name, 'Unknown Taxon'),
  target.organism_group = 'PLANT',
  target.projected_at = datetime()

MERGE (loc:Locality {locality_id: $locality_id})
ON CREATE SET
  loc.projected_at = datetime()

MERGE (src:Source {source_id: $source_id})
ON CREATE SET
  src.source_type = coalesce($source_type, 'PUBLICATION'),
  src.title = coalesce($source_title, 'Scientific Publication'),
  src.publication_year = coalesce($publication_year, 2000),
  src.projected_at = datetime()

MERGE (ev:Evidence {evidence_id: $evidence_id})
ON CREATE SET
  ev.evidence_kind = coalesce($evidence_kind, 'OBSERVATION'),
  ev.method_code = $method_code,
  ev.source_locator = coalesce($source_locator, 'Reference Citation'),
  ev.review_status = 'VERIFIED',
  ev.projected_at = datetime()

MERGE (assertion:InteractionAssertion {assertion_id: $assertion_id})
ON CREATE SET
  assertion.interaction_type_code = $interaction_type_code,
  assertion.period_start = $period_start,
  assertion.period_end = $period_end,
  assertion.season_label = $season_label,
  assertion.method_code = $method_code,
  assertion.status = 'PUBLISHED',
  assertion.assertion_scope = coalesce($assertion_scope, 'BIOLINK_V1'),
  assertion.aggregate_version = coalesce($aggregate_version, 1),
  assertion.projected_at = datetime()
ON MATCH SET
  assertion.interaction_type_code = CASE
    WHEN coalesce(assertion.aggregate_version, 0) <= coalesce($aggregate_version, 1)
    THEN $interaction_type_code ELSE assertion.interaction_type_code END,
  assertion.period_start = CASE
    WHEN coalesce(assertion.aggregate_version, 0) <= coalesce($aggregate_version, 1)
    THEN $period_start ELSE assertion.period_start END,
  assertion.period_end = CASE
    WHEN coalesce(assertion.aggregate_version, 0) <= coalesce($aggregate_version, 1)
    THEN $period_end ELSE assertion.period_end END,
  assertion.season_label = CASE
    WHEN coalesce(assertion.aggregate_version, 0) <= coalesce($aggregate_version, 1)
    THEN $season_label ELSE assertion.season_label END,
  assertion.method_code = CASE
    WHEN coalesce(assertion.aggregate_version, 0) <= coalesce($aggregate_version, 1)
    THEN $method_code ELSE assertion.method_code END,
  assertion.status = 'PUBLISHED',
  assertion.aggregate_version = CASE
    WHEN coalesce(assertion.aggregate_version, 0) <= coalesce($aggregate_version, 1)
    THEN coalesce($aggregate_version, 1) ELSE assertion.aggregate_version END,
  assertion.updated_at = datetime()

MERGE (actor)-[:ACTOR_IN]->(assertion)
MERGE (assertion)-[:TARGETS]->(target)
MERGE (assertion)-[:OBSERVED_AT]->(loc)
MERGE (assertion)-[:SUPPORTED_BY]->(ev)
MERGE (ev)-[:CITED_FROM]->(src)

RETURN assertion.assertion_id AS projected_assertion_id;
"""

_WITHDRAW_ASSERTION_CYPHER = """
MATCH (a:InteractionAssertion {assertion_id: $assertion_id})
WITH a, a.assertion_id AS aid
DETACH DELETE a
RETURN count(aid) AS deleted_count;
"""

# Procesamiento por transacciones acotadas para evitar MemoryPoolOutOfMemoryError (ADR-004)
_WITHDRAW_BATCH_CYPHER = """
UNWIND $batch_assertion_ids AS aid
CALL {
  WITH aid
  MATCH (a:InteractionAssertion {assertion_id: aid})
  DETACH DELETE a
} IN TRANSACTIONS OF 1000 ROWS;
"""

_LIST_PROJECTED_IDS_CYPHER = """
MATCH (a:InteractionAssertion)
RETURN a.assertion_id AS assertion_id;
"""


class Neo4jProjectionRepository:
    """Repositorio Cypher para proyeccion idempotente, retiro controlado y reconciliacion en Neo4j."""

    def __init__(self, provider: Neo4jConnectionProvider) -> None:
        self._provider = provider

    def project_assertion(self, payload: dict[str, Any]) -> str:
        """Proyecta idempotentemente una afirmacion PUBLISHED hacia Neo4j (RD-16)."""
        if payload.get("status") != "PUBLISHED":
            raise BusinessRuleViolationError(
                "RD-16",
                "Solo pueden proyectarse en Neo4j afirmaciones en estado 'PUBLISHED'.",
                details={"assertion_id": payload.get("assertion_id"), "status": payload.get("status")},
            )

        evidence_ids = payload.get("evidence_ids") or []
        if not evidence_ids:
            raise BusinessRuleViolationError(
                "RD-02",
                "No puede proyectarse una afirmacion sin identificador de evidencia.",
                details={"assertion_id": payload.get("assertion_id")},
            )

        params = {
            "status": payload["status"],
            "assertion_id": payload["assertion_id"],
            "actor_taxon_id": payload["actor_taxon_id"],
            "target_taxon_id": payload["target_taxon_id"],
            "interaction_type_code": payload["interaction_type_code"],
            "locality_id": payload["locality_id"],
            "period_start": payload["period_start"],
            "period_end": payload["period_end"],
            "season_label": payload.get("season_label", ""),
            "method_code": payload["method_code"],
            "assertion_scope": payload.get("assertion_scope"),
            "aggregate_version": int(payload.get("aggregate_version", 1)),
            "evidence_id": str(evidence_ids[0]),
            "evidence_kind": payload.get("evidence_kind"),
            "source_locator": payload.get("source_locator"),
            "source_id": payload["source_id"],
            "source_type": payload.get("source_type"),
            "source_title": payload.get("source_title"),
            "publication_year": payload.get("publication_year"),
            "actor_name": payload.get("actor_name"),
            "target_name": payload.get("target_name"),
        }
        try:
            with self._provider.session() as session:
                rec = session.run(_PROJECT_ASSERTION_CYPHER, params).single()
        except (DriverError, Neo4jError) as exc:
            raise DatabaseUnavailableError("neo4j", str(exc)) from exc
        if not rec:
            raise RuntimeError(f"La proyeccion de '{payload['assertion_id']}' no retorno confirmacion.")
        return str(rec["projected_assertion_id"])

    def withdraw_assertion(self, assertion_id: str) -> int:
        """Retira una afirmacion puntual del grafo cuando pasa a WITHDRAWN o REJECTED."""
        try:
            with self._provider.session() as session:
                rec = session.run(_WITHDRAW_ASSERTION_CYPHER, {"assertion_id": assertion_id}).single()
        except (DriverError, Neo4jError) as exc:
            raise DatabaseUnavailableError("neo4j", str(exc)) from exc
        return int(rec["deleted_count"]) if rec else 0

    def withdraw_assertions_batch(self, assertion_ids: list[str]) -> int:
        """Retira multiples afirmaciones usando subconsultas transaccionales de 1000 filas (ADR-004)."""
        if not assertion_ids:
            return 0
        try:
            with self._provider.session() as session:
                summary = session.run(
                    _WITHDRAW_BATCH_CYPHER,
                    {"batch_assertion_ids": assertion_ids},
                ).consume()
        except (DriverError, Neo4jError) as exc:
            raise DatabaseUnavailableError("neo4j", str(exc)) from exc
        return int(summary.counters.nodes_deleted)

    def list_projected_assertion_ids(self) -> set[str]:
        """Lista todos los assertion_id proyectados actualmente en Neo4j."""
        try:
            with self._provider.session() as session:
                records = list(session.run(_LIST_PROJECTED_IDS_CYPHER))
        except (DriverError, Neo4jError) as exc:
            raise DatabaseUnavailableError("neo4j", str(exc)) from exc
        return {str(rec["assertion_id"]) for rec in records if rec["assertion_id"] is not None}

    def reconcile_published_assertions(self, expected_payloads: list[dict[str, Any]]) -> dict[str, int]:
        """Reconcilia Neo4j contra la fuente de verdad PostgreSQL eliminando huerfanas y proyectando faltantes."""
        projected_ids = self.list_projected_assertion_ids()
        expected_ids = {str(item["assertion_id"]) for item in expected_payloads}
        stale_ids = sorted(projected_ids - expected_ids)

        removed_count = 0
        if stale_ids:
            removed_count = self.withdraw_assertions_batch(stale_ids)

        verified_count = 0
        for payload in expected_payloads:
            self.project_assertion(payload)
            verified_count += 1

        return {
            "projected_or_verified": verified_count,
            "removed_stale": removed_count,
        }
