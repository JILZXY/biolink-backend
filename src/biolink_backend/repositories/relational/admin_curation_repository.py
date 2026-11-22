from __future__ import annotations

import hashlib
import json
from datetime import date
from typing import Any

import psycopg

from biolink_backend.application.dto import (
    AdminAssertionDetailDTO,
    AuditLogEntryDTO,
    CreateAssertionInputDTO,
    EvidenceDetailDTO,
    ImportBatchCreateInputDTO,
    ImportBatchDetailDTO,
    ImportBatchDTO,
    ImportRecordDTO,
    LocalitySummaryDTO,
    NormalizeImportRecordInputDTO,
    NormalizedImportRecordResultDTO,
    OutboxEventDTO,
    ProvenanceHistoryResultDTO,
    ReviewAssertionResultDTO,
    ReviewEventDTO,
    SourceSummaryDTO,
    SourceVersionDTO,
    TaxonSummaryDTO,
)
from biolink_backend.domain.entities import Evidence, InteractionAssertion, Taxon
from biolink_backend.domain.rules import (
    ensure_not_occurrence_only,
    validate_interaction_type_for_v1,
    validate_publication_requirements,
    validate_state_transition,
    validate_v1_actor_target_groups,
)
from biolink_backend.domain.value_objects import PartialDateRange
from biolink_backend.domain.vocabularies import (
    AssertionStatus,
    EvidenceKind,
    EvidenceMethodCode,
    ImportRecordValidationStatus,
    InteractionTypeCode,
    OrganismGroup,
    OutboxEventType,
    ProjectionStatus,
    TaxonomicRank,
    TaxonomicStatus,
)
from biolink_backend.infrastructure.postgresql import PostgresConnectionProvider
from biolink_backend.shared.exceptions import (
    BusinessRuleViolationError,
    DatabaseUnavailableError,
    DomainValidationError,
    EntityNotFoundError,
)
from biolink_backend.shared.ids import generate_domain_id

_IMPORT_BATCH_SELECT = """
SELECT
    ib.import_batch_id,
    ib.source_id,
    ib.source_version_id,
    COALESCE(ib.provider_version, sv.provider_version) AS provider_version,
    COALESCE(ib.endpoint_or_file, sv.endpoint_or_file) AS endpoint_or_file,
    ib.started_by,
    ib.started_at,
    ib.finished_at,
    ib.query_parameters,
    ib.filters,
    ib.status,
    ib.received_count,
    ib.accepted_count,
    ib.rejected_count,
    (SELECT COUNT(*) FROM import_record ir WHERE ir.import_batch_id = ib.import_batch_id AND ir.validation_status = 'PENDING') AS pending_count,
    (SELECT COUNT(*) FROM import_record ir WHERE ir.import_batch_id = ib.import_batch_id AND ir.validation_status = 'UNRESOLVED') AS unresolved_count
FROM import_batch ib
LEFT JOIN source_version sv ON sv.source_version_id = ib.source_version_id
"""

_IMPORT_RECORD_SELECT = """
SELECT
    ir.import_record_id,
    ir.import_batch_id,
    ir.external_record_id,
    ir.raw_document_id,
    ir.validation_status,
    ir.canonical_taxon_id,
    t.accepted_name AS canonical_accepted_name,
    ir.verbatim_name,
    ir.proposed_external_scheme,
    ir.proposed_external_id,
    ir.matching_rule,
    ir.validation_notes,
    ir.normalized_by,
    ir.normalized_at,
    ir.created_at
FROM import_record ir
LEFT JOIN taxon t ON t.taxon_id = ir.canonical_taxon_id
"""


def _map_import_batch(row: dict[str, Any]) -> ImportBatchDTO:
    return ImportBatchDTO(
        import_batch_id=row["import_batch_id"],
        source_id=row["source_id"],
        source_version_id=row["source_version_id"],
        provider_version=row["provider_version"],
        endpoint_or_file=row["endpoint_or_file"],
        started_by=row["started_by"],
        started_at=row["started_at"].isoformat(),
        finished_at=row["finished_at"].isoformat() if row["finished_at"] else None,
        query_parameters=dict(row["query_parameters"]),
        filters=dict(row["filters"]),
        status=row["status"],
        received_count=int(row["received_count"]),
        accepted_count=int(row["accepted_count"]),
        rejected_count=int(row["rejected_count"]),
        pending_count=int(row["pending_count"]),
        unresolved_count=int(row["unresolved_count"]),
    )


def _map_import_record(row: dict[str, Any]) -> ImportRecordDTO:
    return ImportRecordDTO(
        import_record_id=row["import_record_id"],
        import_batch_id=row["import_batch_id"],
        external_record_id=row["external_record_id"],
        raw_document_id=row["raw_document_id"],
        validation_status=row["validation_status"],
        canonical_taxon_id=row["canonical_taxon_id"],
        canonical_accepted_name=row["canonical_accepted_name"],
        verbatim_name=row["verbatim_name"],
        proposed_external_scheme=row["proposed_external_scheme"],
        proposed_external_id=row["proposed_external_id"],
        matching_rule=row["matching_rule"],
        validation_notes=row["validation_notes"],
        normalized_by=row["normalized_by"],
        normalized_at=row["normalized_at"].isoformat() if row["normalized_at"] else None,
        created_at=row["created_at"].isoformat(),
    )


def _map_outbox_dto(row: dict[str, Any]) -> OutboxEventDTO:
    return OutboxEventDTO(
        event_id=row["outbox_event_id"],
        event_type=row["event_type"],
        assertion_id=row["aggregate_id"],
        aggregate_version=int(row.get("aggregate_version", 1)),
        payload_version=int(row.get("payload_version", 1)),
        created_at=row["created_at"].isoformat(),
        processing_status=row["status"],
        attempts=int(row["attempts"]),
        last_error=row["error_message"],
        processed_at=row["processed_at"].isoformat() if row["processed_at"] else None,
        payload_json=dict(row["payload_json"]),
    )


def _map_review_event_dto(row: dict[str, Any]) -> ReviewEventDTO:
    return ReviewEventDTO(
        review_event_id=row["review_event_id"],
        assertion_id=row["assertion_id"],
        user_id=row["user_id"],
        previous_status=row["previous_status"],
        new_status=row["new_status"],
        reason=row["reason"],
        created_at=row["created_at"].isoformat(),
    )


def _map_audit_entry(row: dict[str, Any]) -> AuditLogEntryDTO:
    return AuditLogEntryDTO(
        audit_id=int(row["audit_id"]),
        entity_type=row["entity_type"],
        entity_id=row["entity_id"],
        action=row["action"],
        performed_by=row["performed_by"],
        performed_at=row["performed_at"].isoformat(),
        details=dict(row["details"]),
    )


class PostgresAdminCurationRepository:
    """Repositorio SQL nativo (sin ORM) para operaciones administrativas OP-09 a OP-14 y auditoria."""

    def __init__(self, provider: PostgresConnectionProvider) -> None:
        self._provider = provider

    @staticmethod
    def _insert_audit(
        cur: psycopg.Cursor[Any],
        *,
        entity_type: str,
        entity_id: str,
        action: str,
        performed_by: str | None,
        details: dict[str, Any],
    ) -> None:
        cur.execute(
            """
            INSERT INTO audit_log (entity_type, entity_id, action, performed_by, details)
            VALUES (%s, %s, %s, %s, %s::jsonb);
            """,
            (entity_type, entity_id, action, performed_by, json.dumps(details, ensure_ascii=False)),
        )

    @staticmethod
    def _compute_batch_checksum(data: ImportBatchCreateInputDTO) -> str:
        raw = json.dumps(
            {
                "source_id": data.source_id,
                "provider_version": data.provider_version,
                "endpoint_or_file": data.endpoint_or_file,
                "query_parameters": data.query_parameters,
                "filters": data.filters,
                "external_record_ids": [r.external_record_id for r in data.records],
            },
            sort_keys=True,
            ensure_ascii=False,
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _fetch_taxon_entity(self, cur: psycopg.Cursor[Any], taxon_id: str, label: str) -> Taxon:
        cur.execute(
            """
            SELECT taxon_id, accepted_name, scientific_authorship, taxonomic_rank, organism_group, taxonomic_status
            FROM taxon
            WHERE taxon_id = %s;
            """,
            (taxon_id,),
        )
        row = cur.fetchone()
        if not row:
            raise EntityNotFoundError(label, taxon_id)
        return Taxon(
            taxon_id=row["taxon_id"],
            accepted_name=row["accepted_name"],
            scientific_authorship=row["scientific_authorship"],
            taxonomic_rank=TaxonomicRank(row["taxonomic_rank"]),
            organism_group=OrganismGroup(row["organism_group"]),
            taxonomic_status=TaxonomicStatus(row["taxonomic_status"]),
        )

    def _fetch_evidences_any_status(self, cur: psycopg.Cursor[Any], assertion_id: str) -> list[EvidenceDetailDTO]:
        cur.execute(
            """
            SELECT
                e.evidence_id,
                e.source_id,
                e.source_version_id,
                e.source_locator,
                e.evidence_kind,
                e.method_code,
                e.reported_term,
                e.summary,
                e.interpretation_limit,
                e.period_start,
                e.period_end,
                e.license_id,
                ae.evidence_role,
                ae.review_note,
                s.source_type,
                s.title,
                s.authors,
                s.publication_year,
                s.citation_text,
                s.license_id AS source_license_id,
                s.doi,
                s.uri,
                s.publisher
            FROM assertion_evidence ae
            JOIN evidence e ON e.evidence_id = ae.evidence_id
            JOIN source s ON s.source_id = e.source_id
            WHERE ae.assertion_id = %s
            ORDER BY
                CASE ae.evidence_role WHEN 'PRIMARY' THEN 0 WHEN 'SUPPORTING' THEN 1 ELSE 2 END,
                e.evidence_id;
            """,
            (assertion_id,),
        )
        rows = cur.fetchall()
        return [
            EvidenceDetailDTO(
                evidence_id=r["evidence_id"],
                source_id=r["source_id"],
                source_version_id=r["source_version_id"],
                source_locator=r["source_locator"],
                evidence_kind=r["evidence_kind"],
                method_code=r["method_code"],
                reported_term=r["reported_term"],
                summary=r["summary"],
                interpretation_limit=r["interpretation_limit"],
                period_start=r["period_start"],
                period_end=r["period_end"],
                license_id=r["license_id"],
                evidence_role=r["evidence_role"],
                review_note=r["review_note"],
                source=SourceSummaryDTO(
                    source_id=r["source_id"],
                    source_type=r["source_type"],
                    title=r["title"],
                    authors=r["authors"],
                    publication_year=int(r["publication_year"]),
                    citation_text=r["citation_text"],
                    license_id=r["source_license_id"],
                    doi=r["doi"],
                    uri=r["uri"],
                    publisher=r["publisher"],
                ),
            )
            for r in rows
        ]

    def _fetch_admin_assertion_detail(
        self, cur: psycopg.Cursor[Any], assertion_id: str
    ) -> AdminAssertionDetailDTO | None:
        cur.execute(
            """
            SELECT
                ia.assertion_id,
                ia.status,
                ia.aggregate_version,
                ia.projection_status,
                ia.last_projected_at,
                it.code AS interaction_type_code,
                ia.period_start,
                ia.period_end,
                ia.season_label,
                ia.method_code,
                ia.assertion_scope,
                ia.created_by,
                ia.created_at,
                ia.reviewed_by,
                ia.reviewed_at,
                ia.updated_at,
                act.taxon_id AS actor_id,
                act.accepted_name AS actor_name,
                act.scientific_authorship AS actor_auth,
                act.taxonomic_rank AS actor_rank,
                act.organism_group AS actor_group,
                act.taxonomic_status AS actor_status,
                tgt.taxon_id AS target_id,
                tgt.accepted_name AS target_name,
                tgt.scientific_authorship AS target_auth,
                tgt.taxonomic_rank AS target_rank,
                tgt.organism_group AS target_group,
                tgt.taxonomic_status AS target_status,
                loc.locality_id,
                loc.name AS locality_name,
                loc.locality_type,
                loc.protected_status,
                loc.spatial_validation_status,
                loc.coordinate_precision,
                loc.parent_locality_id
            FROM interaction_assertion ia
            JOIN interaction_type it ON it.interaction_type_id = ia.interaction_type_id
            JOIN taxon act ON act.taxon_id = ia.actor_taxon_id
            JOIN taxon tgt ON tgt.taxon_id = ia.target_taxon_id
            JOIN locality loc ON loc.locality_id = ia.locality_id
            WHERE ia.assertion_id = %s;
            """,
            (assertion_id,),
        )
        r = cur.fetchone()
        if not r:
            return None
        evidences = self._fetch_evidences_any_status(cur, assertion_id)
        return AdminAssertionDetailDTO(
            assertion_id=r["assertion_id"],
            status=r["status"],
            aggregate_version=int(r["aggregate_version"]),
            projection_status=r["projection_status"],
            last_projected_at=r["last_projected_at"].isoformat() if r["last_projected_at"] else None,
            interaction_type_code=r["interaction_type_code"],
            actor_taxon=TaxonSummaryDTO(
                taxon_id=r["actor_id"],
                accepted_name=r["actor_name"],
                scientific_authorship=r["actor_auth"],
                taxonomic_rank=r["actor_rank"],
                organism_group=r["actor_group"],
                taxonomic_status=r["actor_status"],
            ),
            target_taxon=TaxonSummaryDTO(
                taxon_id=r["target_id"],
                accepted_name=r["target_name"],
                scientific_authorship=r["target_auth"],
                taxonomic_rank=r["target_rank"],
                organism_group=r["target_group"],
                taxonomic_status=r["target_status"],
            ),
            locality=LocalitySummaryDTO(
                locality_id=r["locality_id"],
                name=r["locality_name"],
                locality_type=r["locality_type"],
                protected_status=r["protected_status"],
                spatial_validation_status=r["spatial_validation_status"],
                coordinate_precision=r["coordinate_precision"],
                parent_locality_id=r["parent_locality_id"],
            ),
            period_start=r["period_start"],
            period_end=r["period_end"],
            season_label=r["season_label"],
            method_code=r["method_code"],
            assertion_scope=r["assertion_scope"],
            created_by=r["created_by"],
            created_at=r["created_at"].isoformat(),
            reviewed_by=r["reviewed_by"],
            reviewed_at=r["reviewed_at"].isoformat() if r["reviewed_at"] else None,
            updated_at=r["updated_at"].isoformat(),
            evidences=evidences,
            graph_projected=r["status"] == "PUBLISHED" and r["projection_status"] == "SYNCED",
        )

    def create_import_batch(
        self,
        *,
        data: ImportBatchCreateInputDTO,
        performed_by: str,
        raw_document_ids: dict[str, str],
        batch_id: str | None = None,
    ) -> ImportBatchDetailDTO:
        """OP-09: Registra lote de importacion, version de fuente y registros PENDING para revision."""
        if data.auto_publish:
            raise BusinessRuleViolationError(
                "RD-13",
                "Los datos importados nunca pueden publicarse automaticamente (OP-09).",
            )
        import_batch_id = batch_id or generate_domain_id("import_batch")
        source_version_id = generate_domain_id("source_version")
        checksum = self._compute_batch_checksum(data)
        release_date_val = date.fromisoformat(data.release_date) if data.release_date else None

        try:
            with self._provider.connection() as conn:
                with conn.transaction():
                    with conn.cursor() as cur:
                        cur.execute(
                            "SELECT source_id, license_id FROM source WHERE source_id = %s;",
                            (data.source_id,),
                        )
                        src_row = cur.fetchone()
                        if not src_row:
                            raise EntityNotFoundError("source", data.source_id)

                        effective_license = data.license_id or src_row["license_id"]

                        cur.execute(
                            """
                            INSERT INTO source_version (
                                source_version_id, source_id, provider_version, release_date,
                                endpoint_or_file, query_parameters, filters, license_id, checksum
                            ) VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, %s);
                            """,
                            (
                                source_version_id,
                                data.source_id,
                                data.provider_version,
                                release_date_val,
                                data.endpoint_or_file,
                                json.dumps(data.query_parameters, ensure_ascii=False),
                                json.dumps(data.filters, ensure_ascii=False),
                                effective_license,
                                checksum,
                            ),
                        )

                        cur.execute(
                            """
                            INSERT INTO import_batch (
                                import_batch_id, source_id, source_version_id, started_by,
                                finished_at, query_parameters, filters, status,
                                received_count, accepted_count, rejected_count,
                                endpoint_or_file, provider_version
                            ) VALUES (
                                %s, %s, %s, %s,
                                clock_timestamp(), %s::jsonb, %s::jsonb, 'COMPLETED',
                                %s, 0, 0,
                                %s, %s
                            );
                            """,
                            (
                                import_batch_id,
                                data.source_id,
                                source_version_id,
                                performed_by,
                                json.dumps(data.query_parameters, ensure_ascii=False),
                                json.dumps(data.filters, ensure_ascii=False),
                                len(data.records),
                                data.endpoint_or_file,
                                data.provider_version,
                            ),
                        )

                        for rec in data.records:
                            rec_id = generate_domain_id("import_record")
                            raw_doc_id = raw_document_ids.get(rec.external_record_id)
                            cur.execute(
                                """
                                INSERT INTO import_record (
                                    import_record_id, import_batch_id, external_record_id, raw_document_id,
                                    validation_status, verbatim_name, proposed_external_scheme, proposed_external_id
                                ) VALUES (%s, %s, %s, %s, 'PENDING', %s, %s, %s);
                                """,
                                (
                                    rec_id,
                                    import_batch_id,
                                    rec.external_record_id,
                                    raw_doc_id,
                                    rec.verbatim_name,
                                    rec.proposed_external_scheme,
                                    rec.proposed_external_id,
                                ),
                            )

                        self._insert_audit(
                            cur,
                            entity_type="import_batch",
                            entity_id=import_batch_id,
                            action="INSERT",
                            performed_by=performed_by,
                            details={
                                "source_id": data.source_id,
                                "source_version_id": source_version_id,
                                "provider_version": data.provider_version,
                                "endpoint_or_file": data.endpoint_or_file,
                                "received_count": len(data.records),
                            },
                        )

                        cur.execute(
                            f"{_IMPORT_BATCH_SELECT} WHERE ib.import_batch_id = %s;",
                            (import_batch_id,),
                        )
                        batch_row = cur.fetchone()
                        cur.execute(
                            f"{_IMPORT_RECORD_SELECT} WHERE ir.import_batch_id = %s ORDER BY ir.created_at ASC, ir.import_record_id ASC;",
                            (import_batch_id,),
                        )
                        record_rows = cur.fetchall()
        except psycopg.Error as exc:
            raise DatabaseUnavailableError("postgresql", str(exc)) from exc

        assert batch_row is not None
        return ImportBatchDetailDTO(
            batch=_map_import_batch(batch_row),
            records=[_map_import_record(r) for r in record_rows],
        )

    def get_import_batch_detail(self, import_batch_id: str) -> ImportBatchDetailDTO | None:
        try:
            with self._provider.connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        f"{_IMPORT_BATCH_SELECT} WHERE ib.import_batch_id = %s;",
                        (import_batch_id,),
                    )
                    batch_row = cur.fetchone()
                    if not batch_row:
                        return None
                    cur.execute(
                        f"{_IMPORT_RECORD_SELECT} WHERE ir.import_batch_id = %s ORDER BY ir.created_at ASC, ir.import_record_id ASC;",
                        (import_batch_id,),
                    )
                    record_rows = cur.fetchall()
        except psycopg.Error as exc:
            raise DatabaseUnavailableError("postgresql", str(exc)) from exc

        return ImportBatchDetailDTO(
            batch=_map_import_batch(batch_row),
            records=[_map_import_record(r) for r in record_rows],
        )

    def list_import_records(
        self,
        *,
        import_batch_id: str | None = None,
        validation_status: str | None = None,
        limit: int = 100,
    ) -> list[ImportRecordDTO]:
        conditions: list[str] = []
        params: list[object] = []
        if import_batch_id:
            conditions.append("ir.import_batch_id = %s")
            params.append(import_batch_id)
        if validation_status:
            conditions.append("ir.validation_status = %s")
            params.append(validation_status)

        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        sql = f"{_IMPORT_RECORD_SELECT} {where_clause} ORDER BY ir.created_at ASC, ir.import_record_id ASC LIMIT %s;"
        params.append(limit)
        try:
            with self._provider.connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(sql, params)
                    rows = cur.fetchall()
        except psycopg.Error as exc:
            raise DatabaseUnavailableError("postgresql", str(exc)) from exc
        return [_map_import_record(r) for r in rows]

    def normalize_import_record(
        self,
        *,
        data: NormalizeImportRecordInputDTO,
        performed_by: str,
    ) -> tuple[NormalizedImportRecordResultDTO, str | None]:
        """OP-10: Valida y normaliza un registro importado conservando verbatim_name y regla de correspondencia."""
        try:
            status_enum = ImportRecordValidationStatus(data.validation_status)
        except ValueError as exc:
            raise DomainValidationError(f"Estado de validacion invalido: '{data.validation_status}'.") from exc
        if status_enum == ImportRecordValidationStatus.VALID and not data.canonical_taxon_id:
            raise DomainValidationError(
                "Un registro con validation_status='VALID' requiere 'canonical_taxon_id'.",
                details={"import_record_id": data.import_record_id},
            )
        if status_enum == ImportRecordValidationStatus.UNRESOLVED and data.canonical_taxon_id is not None:
            raise DomainValidationError(
                "Un registro con validation_status='UNRESOLVED' no debe asociarse a un taxon canonico resuelto.",
                details={"import_record_id": data.import_record_id},
            )

        registered_name_id: str | None = None
        registered_identifier_id: str | None = None
        raw_doc_id: str | None = None

        try:
            with self._provider.connection() as conn:
                with conn.transaction():
                    with conn.cursor() as cur:
                        cur.execute(
                            """
                            SELECT
                                ir.import_record_id,
                                ir.import_batch_id,
                                ir.raw_document_id,
                                ir.verbatim_name,
                                ir.proposed_external_scheme,
                                ir.proposed_external_id,
                                ib.source_id
                            FROM import_record ir
                            JOIN import_batch ib ON ib.import_batch_id = ir.import_batch_id
                            WHERE ir.import_record_id = %s
                            FOR UPDATE OF ir;
                            """,
                            (data.import_record_id,),
                        )
                        rec_row = cur.fetchone()
                        if not rec_row:
                            raise EntityNotFoundError("import_record", data.import_record_id)

                        raw_doc_id = rec_row["raw_document_id"]
                        source_id = rec_row["source_id"]
                        import_batch_id = rec_row["import_batch_id"]
                        eff_verbatim = data.verbatim_name or rec_row["verbatim_name"]
                        eff_scheme = data.external_scheme or rec_row["proposed_external_scheme"]
                        eff_ext_id = data.external_id or rec_row["proposed_external_id"]

                        canonical_taxon: Taxon | None = None
                        if data.canonical_taxon_id:
                            canonical_taxon = self._fetch_taxon_entity(cur, data.canonical_taxon_id, "taxon")

                        if status_enum == ImportRecordValidationStatus.VALID and canonical_taxon is not None:
                            if data.register_name_variant and eff_verbatim and eff_verbatim.strip():
                                clean_v = eff_verbatim.strip()
                                cur.execute(
                                    """
                                    SELECT taxon_name_id FROM taxon_name
                                    WHERE taxon_id = %s AND lower(name) = lower(%s)
                                    LIMIT 1;
                                    """,
                                    (canonical_taxon.taxon_id, clean_v),
                                )
                                existing_name = cur.fetchone()
                                if existing_name:
                                    registered_name_id = existing_name["taxon_name_id"]
                                else:
                                    registered_name_id = generate_domain_id("taxon_name")
                                    cur.execute(
                                        """
                                        INSERT INTO taxon_name (
                                            taxon_name_id, taxon_id, name, name_usage,
                                            language, source_id, is_preferred, verbatim_name
                                        ) VALUES (%s, %s, %s, 'VERBATIM_ORIGINAL', 'la', %s, false, %s);
                                        """,
                                        (
                                            registered_name_id,
                                            canonical_taxon.taxon_id,
                                            clean_v,
                                            source_id,
                                            clean_v,
                                        ),
                                    )

                            if data.register_external_identifier and eff_scheme and eff_ext_id:
                                cur.execute(
                                    """
                                    SELECT taxon_identifier_id, taxon_id
                                    FROM taxon_identifier
                                    WHERE scheme = %s AND external_id = %s;
                                    """,
                                    (eff_scheme, eff_ext_id),
                                )
                                existing_ext = cur.fetchone()
                                if existing_ext:
                                    if existing_ext["taxon_id"] != canonical_taxon.taxon_id:
                                        raise BusinessRuleViolationError(
                                            "RD-14",
                                            f"El identificador externo '{eff_scheme}:{eff_ext_id}' ya pertenece al taxon '{existing_ext['taxon_id']}'.",
                                            details={
                                                "scheme": eff_scheme,
                                                "external_id": eff_ext_id,
                                                "existing_taxon_id": existing_ext["taxon_id"],
                                            },
                                        )
                                    registered_identifier_id = existing_ext["taxon_identifier_id"]
                                else:
                                    registered_identifier_id = generate_domain_id("taxon_identifier")
                                    cur.execute(
                                        """
                                        INSERT INTO taxon_identifier (
                                            taxon_identifier_id, taxon_id, scheme, external_id, source_id
                                        ) VALUES (%s, %s, %s, %s, %s);
                                        """,
                                        (
                                            registered_identifier_id,
                                            canonical_taxon.taxon_id,
                                            eff_scheme,
                                            eff_ext_id,
                                            source_id,
                                        ),
                                    )

                        cur.execute(
                            """
                            UPDATE import_record
                            SET validation_status = %s,
                                canonical_taxon_id = %s,
                                verbatim_name = %s,
                                proposed_external_scheme = %s,
                                proposed_external_id = %s,
                                matching_rule = %s,
                                validation_notes = %s,
                                normalized_by = %s,
                                normalized_at = clock_timestamp()
                            WHERE import_record_id = %s;
                            """,
                            (
                                status_enum.value,
                                data.canonical_taxon_id,
                                eff_verbatim,
                                eff_scheme,
                                eff_ext_id,
                                data.matching_rule,
                                data.validation_notes,
                                performed_by,
                                data.import_record_id,
                            ),
                        )

                        # Actualizar conteos agregados del lote
                        cur.execute(
                            """
                            UPDATE import_batch
                            SET accepted_count = (
                                    SELECT COUNT(*) FROM import_record
                                    WHERE import_batch_id = %s AND validation_status = 'VALID'
                                ),
                                rejected_count = (
                                    SELECT COUNT(*) FROM import_record
                                    WHERE import_batch_id = %s AND validation_status IN ('INVALID', 'DUPLICATE')
                                ),
                                status = CASE
                                    WHEN EXISTS (
                                        SELECT 1 FROM import_record
                                        WHERE import_batch_id = %s AND validation_status IN ('PENDING', 'UNRESOLVED')
                                    ) THEN 'PARTIAL'
                                    ELSE 'COMPLETED'
                                END
                            WHERE import_batch_id = %s;
                            """,
                            (import_batch_id, import_batch_id, import_batch_id, import_batch_id),
                        )

                        self._insert_audit(
                            cur,
                            entity_type="import_record",
                            entity_id=data.import_record_id,
                            action="UPDATE",
                            performed_by=performed_by,
                            details={
                                "import_batch_id": import_batch_id,
                                "validation_status": status_enum.value,
                                "canonical_taxon_id": data.canonical_taxon_id,
                                "verbatim_name": eff_verbatim,
                                "external_scheme": eff_scheme,
                                "external_id": eff_ext_id,
                                "matching_rule": data.matching_rule,
                                "validation_notes": data.validation_notes,
                            },
                        )

                        cur.execute(
                            f"{_IMPORT_RECORD_SELECT} WHERE ir.import_record_id = %s;",
                            (data.import_record_id,),
                        )
                        updated_rec = cur.fetchone()
                        cur.execute(
                            f"{_IMPORT_BATCH_SELECT} WHERE ib.import_batch_id = %s;",
                            (import_batch_id,),
                        )
                        updated_batch = cur.fetchone()
        except psycopg.Error as exc:
            raise DatabaseUnavailableError("postgresql", str(exc)) from exc

        assert updated_rec is not None and updated_batch is not None
        return (
            NormalizedImportRecordResultDTO(
                record=_map_import_record(updated_rec),
                batch=_map_import_batch(updated_batch),
                registered_name_id=registered_name_id,
                registered_identifier_id=registered_identifier_id,
                raw_document_updated=False,
            ),
            raw_doc_id,
        )

    def create_interaction_assertion(
        self,
        *,
        data: CreateAssertionInputDTO,
        performed_by: str,
    ) -> AdminAssertionDetailDTO:
        """OP-11: Crea una afirmacion en estado DRAFT o IN_REVIEW con evidencia explicita (RD-01..RD-06)."""
        try:
            initial_status = AssertionStatus(data.initial_status)
            it_code = InteractionTypeCode(data.interaction_type_code)
            method = EvidenceMethodCode(data.method_code)
        except ValueError as exc:
            raise DomainValidationError(str(exc)) from exc

        if initial_status not in (AssertionStatus.DRAFT, AssertionStatus.IN_REVIEW):
            raise BusinessRuleViolationError(
                "RD-13",
                "OP-11 solo permite crear afirmaciones en estado 'DRAFT' o 'IN_REVIEW'; "
                "la publicacion requiere aprobacion formal en OP-12.",
                details={"initial_status": initial_status.value},
            )

        ensure_not_occurrence_only(bool(data.evidences))
        validate_interaction_type_for_v1(it_code)
        period = PartialDateRange(data.period_start, data.period_end)

        assertion_id = generate_domain_id("interaction_assertion")

        try:
            with self._provider.connection() as conn:
                with conn.transaction():
                    with conn.cursor() as cur:
                        actor = self._fetch_taxon_entity(cur, data.actor_taxon_id, "actor_taxon")
                        target = self._fetch_taxon_entity(cur, data.target_taxon_id, "target_taxon")
                        validate_v1_actor_target_groups(actor, target)

                        cur.execute(
                            """
                            SELECT interaction_type_id, is_active
                            FROM interaction_type
                            WHERE code = %s;
                            """,
                            (it_code.value,),
                        )
                        it_row = cur.fetchone()
                        if not it_row or not it_row["is_active"]:
                            raise BusinessRuleViolationError(
                                "RD-05",
                                f"El tipo de interaccion '{it_code.value}' no existe o esta inactivo.",
                            )

                        cur.execute(
                            "SELECT locality_id FROM locality WHERE locality_id = %s;",
                            (data.locality_id,),
                        )
                        if not cur.fetchone():
                            raise EntityNotFoundError("locality", data.locality_id)

                        resolved_evidence_links: list[tuple[str, str, str | None]] = []
                        for ev_in in data.evidences:
                            if ev_in.evidence_id:
                                cur.execute(
                                    "SELECT evidence_id FROM evidence WHERE evidence_id = %s;",
                                    (ev_in.evidence_id,),
                                )
                                if not cur.fetchone():
                                    raise EntityNotFoundError("evidence", ev_in.evidence_id)
                                resolved_evidence_links.append(
                                    (ev_in.evidence_id, ev_in.evidence_role, ev_in.review_note)
                                )
                            else:
                                if not ev_in.source_id or not ev_in.source_locator or not ev_in.summary or not ev_in.interpretation_limit:
                                    raise DomainValidationError(
                                        "Cada evidencia nueva requiere source_id, source_locator, summary e interpretation_limit."
                                    )
                                cur.execute(
                                    "SELECT source_id, license_id FROM source WHERE source_id = %s;",
                                    (ev_in.source_id,),
                                )
                                src_row = cur.fetchone()
                                if not src_row:
                                    raise EntityNotFoundError("source", ev_in.source_id)
                                ev_period = PartialDateRange(
                                    ev_in.period_start or period.period_start,
                                    ev_in.period_end or period.period_end,
                                )
                                ev_kind = EvidenceKind(ev_in.evidence_kind or EvidenceKind.OBSERVATION.value)
                                ev_method = EvidenceMethodCode(ev_in.method_code or method.value)
                                new_ev_id = generate_domain_id("evidence")
                                cur.execute(
                                    """
                                    INSERT INTO evidence (
                                        evidence_id, source_id, source_version_id, source_locator,
                                        evidence_kind, method_code, reported_term, summary,
                                        interpretation_limit, period_start, period_end, license_id
                                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s);
                                    """,
                                    (
                                        new_ev_id,
                                        ev_in.source_id,
                                        ev_in.source_version_id,
                                        ev_in.source_locator,
                                        ev_kind.value,
                                        ev_method.value,
                                        ev_in.reported_term or it_code.value,
                                        ev_in.summary,
                                        ev_in.interpretation_limit,
                                        ev_period.period_start,
                                        ev_period.period_end,
                                        ev_in.license_id or src_row["license_id"],
                                    ),
                                )
                                resolved_evidence_links.append(
                                    (new_ev_id, ev_in.evidence_role, ev_in.review_note)
                                )

                        cur.execute(
                            """
                            INSERT INTO interaction_assertion (
                                assertion_id, actor_taxon_id, target_taxon_id, interaction_type_id,
                                locality_id, period_start, period_end, season_label, method_code,
                                assertion_scope, status, created_by, aggregate_version, projection_status
                            ) VALUES (
                                %s, %s, %s, %s,
                                %s, %s, %s, %s, %s,
                                %s, %s, %s, 1, 'NOT_APPLICABLE'
                            );
                            """,
                            (
                                assertion_id,
                                actor.taxon_id,
                                target.taxon_id,
                                it_row["interaction_type_id"],
                                data.locality_id,
                                period.period_start,
                                period.period_end,
                                data.season_label,
                                method.value,
                                data.assertion_scope,
                                initial_status.value,
                                performed_by,
                            ),
                        )

                        for ev_id, ev_role, ev_note in resolved_evidence_links:
                            cur.execute(
                                """
                                INSERT INTO assertion_evidence (assertion_id, evidence_id, evidence_role, review_note)
                                VALUES (%s, %s, %s, %s);
                                """,
                                (assertion_id, ev_id, ev_role, ev_note),
                            )

                        if initial_status == AssertionStatus.IN_REVIEW:
                            cur.execute(
                                """
                                INSERT INTO review_event (
                                    review_event_id, assertion_id, user_id, previous_status, new_status, reason
                                ) VALUES (%s, %s, %s, 'DRAFT', 'IN_REVIEW', %s);
                                """,
                                (
                                    generate_domain_id("review_event"),
                                    assertion_id,
                                    performed_by,
                                    data.submission_reason or "Envio inicial a revision cientifica (OP-11)",
                                ),
                            )

                        self._insert_audit(
                            cur,
                            entity_type="interaction_assertion",
                            entity_id=assertion_id,
                            action="INSERT",
                            performed_by=performed_by,
                            details={
                                "status": initial_status.value,
                                "actor_taxon_id": actor.taxon_id,
                                "target_taxon_id": target.taxon_id,
                                "interaction_type_code": it_code.value,
                                "locality_id": data.locality_id,
                                "evidence_ids": [item[0] for item in resolved_evidence_links],
                            },
                        )

                        detail = self._fetch_admin_assertion_detail(cur, assertion_id)
        except psycopg.Error as exc:
            raise DatabaseUnavailableError("postgresql", str(exc)) from exc

        assert detail is not None
        return detail

    def get_assertion_admin_detail(self, assertion_id: str) -> AdminAssertionDetailDTO | None:
        try:
            with self._provider.connection() as conn:
                with conn.cursor() as cur:
                    return self._fetch_admin_assertion_detail(cur, assertion_id)
        except psycopg.Error as exc:
            raise DatabaseUnavailableError("postgresql", str(exc)) from exc

    def transition_assertion_status(
        self,
        *,
        assertion_id: str,
        new_status: str,
        reason: str,
        performed_by: str,
    ) -> ReviewAssertionResultDTO:
        """OP-12: Transicion de estado atomica con review_event, outbox_event y audit_log."""
        target_status = AssertionStatus(new_status)
        if not reason or not reason.strip():
            raise DomainValidationError("Toda transicion de estado requiere motivo explicito.")

        try:
            with self._provider.connection() as conn:
                with conn.transaction():
                    with conn.cursor() as cur:
                        cur.execute(
                            """
                            SELECT
                                ia.assertion_id,
                                ia.actor_taxon_id,
                                ia.target_taxon_id,
                                it.code AS interaction_type_code,
                                ia.locality_id,
                                ia.period_start,
                                ia.period_end,
                                ia.season_label,
                                ia.method_code,
                                ia.assertion_scope,
                                ia.status,
                                ia.created_by,
                                ia.created_at,
                                ia.aggregate_version,
                                act.accepted_name AS actor_name,
                                tgt.accepted_name AS target_name
                            FROM interaction_assertion ia
                            JOIN interaction_type it ON it.interaction_type_id = ia.interaction_type_id
                            JOIN taxon act ON act.taxon_id = ia.actor_taxon_id
                            JOIN taxon tgt ON tgt.taxon_id = ia.target_taxon_id
                            WHERE ia.assertion_id = %s
                            FOR UPDATE OF ia;
                            """,
                            (assertion_id,),
                        )
                        row = cur.fetchone()
                        if not row:
                            raise EntityNotFoundError("interaction_assertion", assertion_id)

                        current_status = AssertionStatus(row["status"])
                        validate_state_transition(current_status, target_status)

                        evidences_detail = self._fetch_evidences_any_status(cur, assertion_id)
                        domain_evidences = [
                            Evidence(
                                evidence_id=e.evidence_id,
                                source_id=e.source_id,
                                source_locator=e.source_locator,
                                evidence_kind=EvidenceKind(e.evidence_kind),
                                method_code=EvidenceMethodCode(e.method_code),
                                reported_term=e.reported_term,
                                summary=e.summary,
                                interpretation_limit=e.interpretation_limit,
                                period=PartialDateRange(e.period_start, e.period_end),
                                license_id=e.license_id,
                                source_version_id=e.source_version_id,
                            )
                            for e in evidences_detail
                        ]
                        domain_assertion = InteractionAssertion(
                            assertion_id=row["assertion_id"],
                            actor_taxon_id=row["actor_taxon_id"],
                            target_taxon_id=row["target_taxon_id"],
                            interaction_type_code=InteractionTypeCode(row["interaction_type_code"]),
                            locality_id=row["locality_id"],
                            period=PartialDateRange(row["period_start"], row["period_end"]),
                            season_label=row["season_label"],
                            method_code=EvidenceMethodCode(row["method_code"]),
                            assertion_scope=row["assertion_scope"],
                            status=current_status,
                            created_by=row["created_by"],
                            created_at=row["created_at"],
                        )

                        if target_status == AssertionStatus.PUBLISHED:
                            validate_publication_requirements(domain_assertion, domain_evidences, performed_by)

                        next_version = int(row["aggregate_version"]) + 1
                        outbox_type: OutboxEventType | None = None
                        new_projection_status = ProjectionStatus.NOT_APPLICABLE.value

                        if target_status == AssertionStatus.PUBLISHED:
                            outbox_type = OutboxEventType.ASSERTION_PUBLISHED
                            new_projection_status = ProjectionStatus.PENDING.value
                        elif (
                            current_status == AssertionStatus.PUBLISHED
                            and target_status == AssertionStatus.WITHDRAWN
                        ):
                            outbox_type = OutboxEventType.ASSERTION_WITHDRAWN
                            new_projection_status = ProjectionStatus.PENDING.value

                        set_reviewer = target_status in (
                            AssertionStatus.PUBLISHED,
                            AssertionStatus.REJECTED,
                            AssertionStatus.WITHDRAWN,
                        )
                        if set_reviewer:
                            cur.execute(
                                """
                                UPDATE interaction_assertion
                                SET status = %s,
                                    aggregate_version = %s,
                                    projection_status = %s,
                                    reviewed_by = %s,
                                    reviewed_at = clock_timestamp(),
                                    updated_at = clock_timestamp()
                                WHERE assertion_id = %s;
                                """,
                                (
                                    target_status.value,
                                    next_version,
                                    new_projection_status,
                                    performed_by,
                                    assertion_id,
                                ),
                            )
                        else:
                            cur.execute(
                                """
                                UPDATE interaction_assertion
                                SET status = %s,
                                    aggregate_version = %s,
                                    projection_status = %s,
                                    updated_at = clock_timestamp()
                                WHERE assertion_id = %s;
                                """,
                                (
                                    target_status.value,
                                    next_version,
                                    new_projection_status,
                                    assertion_id,
                                ),
                            )

                        review_event_id = generate_domain_id("review_event")
                        cur.execute(
                            """
                            INSERT INTO review_event (
                                review_event_id, assertion_id, user_id, previous_status, new_status, reason
                            ) VALUES (%s, %s, %s, %s, %s, %s)
                            RETURNING review_event_id, assertion_id, user_id, previous_status, new_status, reason, created_at;
                            """,
                            (
                                review_event_id,
                                assertion_id,
                                performed_by,
                                current_status.value,
                                target_status.value,
                                reason.strip(),
                            ),
                        )
                        rev_row = cur.fetchone()
                        assert rev_row is not None

                        outbox_dto: OutboxEventDTO | None = None
                        if outbox_type is not None:
                            outbox_id = generate_domain_id("outbox_event")
                            primary_ev = evidences_detail[0] if evidences_detail else None
                            payload: dict[str, Any] = {
                                "event_id": outbox_id,
                                "event_type": outbox_type.value,
                                "assertion_id": assertion_id,
                                "aggregate_version": next_version,
                                "payload_version": 1,
                                "actor_taxon_id": row["actor_taxon_id"],
                                "actor_name": row["actor_name"],
                                "target_taxon_id": row["target_taxon_id"],
                                "target_name": row["target_name"],
                                "interaction_type_code": row["interaction_type_code"],
                                "locality_id": row["locality_id"],
                                "period_start": row["period_start"],
                                "period_end": row["period_end"],
                                "season_label": row["season_label"],
                                "method_code": row["method_code"],
                                "assertion_scope": row["assertion_scope"],
                                "status": target_status.value,
                                "evidence_ids": [e.evidence_id for e in evidences_detail],
                                "evidence_kind": primary_ev.evidence_kind if primary_ev else None,
                                "source_locator": primary_ev.source_locator if primary_ev else None,
                                "source_id": primary_ev.source_id if primary_ev else None,
                                "source_type": primary_ev.source.source_type if primary_ev else None,
                                "source_title": primary_ev.source.title if primary_ev else None,
                                "publication_year": primary_ev.source.publication_year if primary_ev else None,
                            }
                            cur.execute(
                                """
                                INSERT INTO outbox_event (
                                    outbox_event_id, aggregate_type, aggregate_id, event_type,
                                    aggregate_version, payload_version, payload_json, status, attempts
                                ) VALUES (%s, 'InteractionAssertion', %s, %s, %s, 1, %s::jsonb, 'PENDING', 0)
                                RETURNING outbox_event_id, event_type, aggregate_id, aggregate_version,
                                          payload_version, created_at, status, attempts, error_message,
                                          processed_at, payload_json;
                                """,
                                (
                                    outbox_id,
                                    assertion_id,
                                    outbox_type.value,
                                    next_version,
                                    json.dumps(payload, ensure_ascii=False),
                                ),
                            )
                            obx_row = cur.fetchone()
                            assert obx_row is not None
                            outbox_dto = _map_outbox_dto(obx_row)

                        self._insert_audit(
                            cur,
                            entity_type="interaction_assertion",
                            entity_id=assertion_id,
                            action="UPDATE",
                            performed_by=performed_by,
                            details={
                                "previous_status": current_status.value,
                                "new_status": target_status.value,
                                "aggregate_version": next_version,
                                "projection_status": new_projection_status,
                                "outbox_event_id": outbox_dto.event_id if outbox_dto else None,
                                "reason": reason.strip(),
                            },
                        )

                        detail = self._fetch_admin_assertion_detail(cur, assertion_id)
                        assert detail is not None
        except psycopg.Error as exc:
            raise DatabaseUnavailableError("postgresql", str(exc)) from exc

        sync_note = (
            "Transaccion relacional confirmada; evento Outbox PENDING para sincronizacion asincrona con Neo4j."
            if outbox_dto is not None
            else "Transicion relacional confirmada sin proyeccion pendiente hacia Neo4j."
        )
        return ReviewAssertionResultDTO(
            assertion=detail,
            review_event=_map_review_event_dto(rev_row),
            outbox_event=outbox_dto,
            relational_committed=True,
            graph_sync_note=sync_note,
        )

    def ensure_taxon_and_sources_exist(self, taxon_id: str, source_ids: list[str]) -> TaxonSummaryDTO:
        """Verifica que el taxon y todas las fuentes citadas en OP-13 existan en PostgreSQL."""
        try:
            with self._provider.connection() as conn:
                with conn.cursor() as cur:
                    taxon = self._fetch_taxon_entity(cur, taxon_id, "taxon")
                    unique_sources = sorted({s.strip() for s in source_ids if s and s.strip()})
                    if unique_sources:
                        cur.execute(
                            "SELECT source_id FROM source WHERE source_id = ANY(%s);",
                            (unique_sources,),
                        )
                        found = {r["source_id"] for r in cur.fetchall()}
                        missing = [s for s in unique_sources if s not in found]
                        if missing:
                            raise EntityNotFoundError("source", missing[0])
        except psycopg.Error as exc:
            raise DatabaseUnavailableError("postgresql", str(exc)) from exc

        return TaxonSummaryDTO(
            taxon_id=taxon.taxon_id,
            accepted_name=taxon.accepted_name,
            scientific_authorship=taxon.scientific_authorship,
            taxonomic_rank=taxon.taxonomic_rank.value,
            organism_group=taxon.organism_group.value,
            taxonomic_status=taxon.taxonomic_status.value,
        )

    def record_taxon_dossier_version(
        self,
        *,
        taxon_id: str,
        dossier_version: int,
        sync_status: str,
        performed_by: str,
        details: dict[str, Any],
    ) -> None:
        try:
            with self._provider.connection() as conn:
                with conn.transaction():
                    with conn.cursor() as cur:
                        cur.execute(
                            """
                            UPDATE taxon
                            SET dossier_version = %s,
                                dossier_sync_status = %s,
                                updated_at = clock_timestamp()
                            WHERE taxon_id = %s;
                            """,
                            (dossier_version, sync_status, taxon_id),
                        )
                        self._insert_audit(
                            cur,
                            entity_type="taxon_dossier",
                            entity_id=taxon_id,
                            action="UPDATE",
                            performed_by=performed_by,
                            details=details,
                        )
        except psycopg.Error as exc:
            raise DatabaseUnavailableError("postgresql", str(exc)) from exc

    def mark_taxon_dossier_divergence(
        self,
        *,
        taxon_id: str,
        sync_status: str,
        error_detail: str,
        performed_by: str,
    ) -> None:
        try:
            with self._provider.connection() as conn:
                with conn.transaction():
                    with conn.cursor() as cur:
                        cur.execute(
                            """
                            UPDATE taxon
                            SET dossier_sync_status = %s,
                                updated_at = clock_timestamp()
                            WHERE taxon_id = %s;
                            """,
                            (sync_status, taxon_id),
                        )
                        self._insert_audit(
                            cur,
                            entity_type="taxon_dossier",
                            entity_id=taxon_id,
                            action="SECURITY",
                            performed_by=performed_by,
                            details={
                                "sync_status": sync_status,
                                "error_detail": error_detail[:500],
                            },
                        )
        except psycopg.Error as exc:
            raise DatabaseUnavailableError("postgresql", str(exc)) from exc

    def list_taxa_with_dossier_state(self) -> list[tuple[str, int, str]]:
        try:
            with self._provider.connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT taxon_id, dossier_version, dossier_sync_status FROM taxon ORDER BY taxon_id;"
                    )
                    rows = cur.fetchall()
        except psycopg.Error as exc:
            raise DatabaseUnavailableError("postgresql", str(exc)) from exc
        return [
            (r["taxon_id"], int(r["dossier_version"]), r["dossier_sync_status"])
            for r in rows
        ]

    def list_all_published_assertion_payloads(self) -> list[dict[str, Any]]:
        sql = """
        SELECT
            a.assertion_id,
            a.actor_taxon_id,
            a.target_taxon_id,
            it.code AS interaction_type_code,
            a.locality_id,
            a.period_start,
            a.period_end,
            a.season_label,
            a.method_code,
            a.assertion_scope,
            a.status,
            a.aggregate_version,
            act.accepted_name AS actor_name,
            tgt.accepted_name AS target_name,
            COALESCE(
                array_agg(e.evidence_id ORDER BY CASE ae.evidence_role WHEN 'PRIMARY' THEN 0 ELSE 1 END, e.evidence_id),
                ARRAY[]::varchar[]
            ) AS evidence_ids,
            (array_agg(e.evidence_kind ORDER BY CASE ae.evidence_role WHEN 'PRIMARY' THEN 0 ELSE 1 END, e.evidence_id))[1] AS evidence_kind,
            (array_agg(e.source_locator ORDER BY CASE ae.evidence_role WHEN 'PRIMARY' THEN 0 ELSE 1 END, e.evidence_id))[1] AS source_locator,
            (array_agg(s.source_id ORDER BY CASE ae.evidence_role WHEN 'PRIMARY' THEN 0 ELSE 1 END, e.evidence_id))[1] AS source_id,
            (array_agg(s.source_type ORDER BY CASE ae.evidence_role WHEN 'PRIMARY' THEN 0 ELSE 1 END, e.evidence_id))[1] AS source_type,
            (array_agg(s.title ORDER BY CASE ae.evidence_role WHEN 'PRIMARY' THEN 0 ELSE 1 END, e.evidence_id))[1] AS source_title,
            (array_agg(s.publication_year ORDER BY CASE ae.evidence_role WHEN 'PRIMARY' THEN 0 ELSE 1 END, e.evidence_id))[1] AS publication_year
        FROM interaction_assertion a
        JOIN interaction_type it ON it.interaction_type_id = a.interaction_type_id
        JOIN taxon act ON act.taxon_id = a.actor_taxon_id
        JOIN taxon tgt ON tgt.taxon_id = a.target_taxon_id
        JOIN assertion_evidence ae ON ae.assertion_id = a.assertion_id
        JOIN evidence e ON e.evidence_id = ae.evidence_id
        JOIN source s ON s.source_id = e.source_id
        WHERE a.status = 'PUBLISHED'
        GROUP BY a.assertion_id, it.code, act.accepted_name, tgt.accepted_name
        ORDER BY a.assertion_id;
        """
        try:
            with self._provider.connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(sql)
                    rows = cur.fetchall()
        except psycopg.Error as exc:
            raise DatabaseUnavailableError("postgresql", str(exc)) from exc
        return [dict(r) for r in rows]

    def mark_assertions_projection_synced(self, assertion_ids: list[str]) -> int:
        if not assertion_ids:
            return 0
        try:
            with self._provider.connection() as conn:
                with conn.transaction():
                    with conn.cursor() as cur:
                        cur.execute(
                            """
                            UPDATE interaction_assertion
                            SET projection_status = 'SYNCED',
                                last_projected_at = clock_timestamp()
                            WHERE assertion_id = ANY(%s)
                              AND status IN ('PUBLISHED', 'WITHDRAWN');
                            """,
                            (assertion_ids,),
                        )
                        return int(cur.rowcount)
        except psycopg.Error as exc:
            raise DatabaseUnavailableError("postgresql", str(exc)) from exc

    def _fetch_sources_by_ids(self, cur: psycopg.Cursor[Any], source_ids: list[str]) -> list[SourceSummaryDTO]:
        if not source_ids:
            return []
        cur.execute(
            """
            SELECT source_id, source_type, title, authors, publication_year, citation_text, license_id, doi, uri, publisher
            FROM source
            WHERE source_id = ANY(%s)
            ORDER BY source_id;
            """,
            (list(set(source_ids)),),
        )
        return [
            SourceSummaryDTO(
                source_id=r["source_id"],
                source_type=r["source_type"],
                title=r["title"],
                authors=r["authors"],
                publication_year=int(r["publication_year"]),
                citation_text=r["citation_text"],
                license_id=r["license_id"],
                doi=r["doi"],
                uri=r["uri"],
                publisher=r["publisher"],
            )
            for r in cur.fetchall()
        ]

    def _fetch_source_versions_by_ids(
        self, cur: psycopg.Cursor[Any], source_ids: list[str], source_version_ids: list[str]
    ) -> list[SourceVersionDTO]:
        if not source_ids and not source_version_ids:
            return []
        cur.execute(
            """
            SELECT source_version_id, provider_version, release_date, retrieved_at,
                   endpoint_or_file, query_parameters, filters, license_id, checksum
            FROM source_version
            WHERE source_id = ANY(%s) OR source_version_id = ANY(%s)
            ORDER BY retrieved_at ASC, source_version_id ASC;
            """,
            (list(set(source_ids)), list(set(source_version_ids))),
        )
        return [
            SourceVersionDTO(
                source_version_id=r["source_version_id"],
                provider_version=r["provider_version"],
                release_date=r["release_date"].isoformat() if r["release_date"] else None,
                retrieved_at=r["retrieved_at"].isoformat(),
                endpoint_or_file=r["endpoint_or_file"],
                query_parameters=dict(r["query_parameters"]),
                filters=dict(r["filters"]),
                license_id=r["license_id"],
                checksum=r["checksum"],
            )
            for r in cur.fetchall()
        ]

    def get_provenance_history(
        self, *, entity_type: str, entity_id: str
    ) -> ProvenanceHistoryResultDTO | None:
        """OP-14: Reconstruye la cronologia de fuentes, lotes, normalizaciones, revisiones y outbox."""
        normalized_type = entity_type.strip().lower()
        try:
            with self._provider.connection() as conn:
                with conn.cursor() as cur:
                    if normalized_type == "assertion":
                        detail = self._fetch_admin_assertion_detail(cur, entity_id)
                        if not detail:
                            return None
                        current_state = {
                            "assertion_id": detail.assertion_id,
                            "status": detail.status,
                            "aggregate_version": detail.aggregate_version,
                            "projection_status": detail.projection_status,
                            "created_by": detail.created_by,
                            "reviewed_by": detail.reviewed_by,
                        }
                        evidences = detail.evidences
                        source_ids = [e.source_id for e in evidences]
                        sv_ids = [e.source_version_id for e in evidences if e.source_version_id]

                        cur.execute(
                            """
                            SELECT review_event_id, assertion_id, user_id, previous_status, new_status, reason, created_at
                            FROM review_event
                            WHERE assertion_id = %s
                            ORDER BY created_at ASC, review_event_id ASC;
                            """,
                            (entity_id,),
                        )
                        review_events = [_map_review_event_dto(r) for r in cur.fetchall()]

                        cur.execute(
                            """
                            SELECT outbox_event_id, event_type, aggregate_id, aggregate_version, payload_version,
                                   created_at, status, attempts, error_message, processed_at, payload_json
                            FROM outbox_event
                            WHERE aggregate_id = %s
                            ORDER BY created_at ASC, outbox_event_id ASC;
                            """,
                            (entity_id,),
                        )
                        outbox_events = [_map_outbox_dto(r) for r in cur.fetchall()]

                        import_batches: list[ImportBatchDTO] = []
                        import_records: list[ImportRecordDTO] = []
                        if sv_ids:
                            cur.execute(
                                f"{_IMPORT_BATCH_SELECT} WHERE ib.source_version_id = ANY(%s) ORDER BY ib.started_at ASC;",
                                (sv_ids,),
                            )
                            batch_rows = cur.fetchall()
                            import_batches = [_map_import_batch(r) for r in batch_rows]
                            if import_batches:
                                cur.execute(
                                    f"{_IMPORT_RECORD_SELECT} WHERE ir.import_batch_id = ANY(%s) ORDER BY ir.created_at ASC;",
                                    ([b.import_batch_id for b in import_batches],),
                                )
                                import_records = [_map_import_record(r) for r in cur.fetchall()]

                        audit_ids = [entity_id, *[b.import_batch_id for b in import_batches]]
                        cur.execute(
                            """
                            SELECT audit_id, entity_type, entity_id, action, performed_by, performed_at, details
                            FROM audit_log
                            WHERE entity_id = ANY(%s)
                            ORDER BY performed_at ASC, audit_id ASC;
                            """,
                            (audit_ids,),
                        )
                        audit_logs = [_map_audit_entry(r) for r in cur.fetchall()]
                        sources = self._fetch_sources_by_ids(cur, source_ids)
                        source_versions = self._fetch_source_versions_by_ids(cur, source_ids, sv_ids)
                        return ProvenanceHistoryResultDTO(
                            entity_type="assertion",
                            entity_id=entity_id,
                            current_state=current_state,
                            sources=sources,
                            source_versions=source_versions,
                            import_batches=import_batches,
                            import_records=import_records,
                            evidences=evidences,
                            review_events=review_events,
                            outbox_events=outbox_events,
                            dossier_versions=[],
                            audit_logs=audit_logs,
                        )

                    if normalized_type == "taxon":
                        cur.execute(
                            """
                            SELECT taxon_id, accepted_name, scientific_authorship, taxonomic_rank,
                                   organism_group, taxonomic_status, dossier_version, dossier_sync_status
                            FROM taxon
                            WHERE taxon_id = %s;
                            """,
                            (entity_id,),
                        )
                        t_row = cur.fetchone()
                        if not t_row:
                            return None
                        current_state = dict(t_row)

                        cur.execute(
                            f"{_IMPORT_RECORD_SELECT} WHERE ir.canonical_taxon_id = %s ORDER BY ir.created_at ASC;",
                            (entity_id,),
                        )
                        rec_rows = cur.fetchall()
                        import_records = [_map_import_record(r) for r in rec_rows]
                        batch_ids = sorted({r.import_batch_id for r in import_records})

                        import_batches = []
                        if batch_ids:
                            cur.execute(
                                f"{_IMPORT_BATCH_SELECT} WHERE ib.import_batch_id = ANY(%s) ORDER BY ib.started_at ASC;",
                                (batch_ids,),
                            )
                            import_batches = [_map_import_batch(r) for r in cur.fetchall()]

                        cur.execute(
                            """
                            SELECT DISTINCT e.source_id, e.source_version_id
                            FROM interaction_assertion ia
                            JOIN assertion_evidence ae ON ae.assertion_id = ia.assertion_id
                            JOIN evidence e ON e.evidence_id = ae.evidence_id
                            WHERE ia.actor_taxon_id = %s OR ia.target_taxon_id = %s;
                            """,
                            (entity_id, entity_id),
                        )
                        src_rows = cur.fetchall()
                        source_ids = [b.source_id for b in import_batches] + [r["source_id"] for r in src_rows]
                        sv_ids = [b.source_version_id for b in import_batches if b.source_version_id] + [
                            r["source_version_id"] for r in src_rows if r["source_version_id"]
                        ]

                        audit_targets = [
                            entity_id,
                            *[r.import_record_id for r in import_records],
                            *batch_ids,
                        ]
                        cur.execute(
                            """
                            SELECT audit_id, entity_type, entity_id, action, performed_by, performed_at, details
                            FROM audit_log
                            WHERE entity_id = ANY(%s)
                            ORDER BY performed_at ASC, audit_id ASC;
                            """,
                            (audit_targets,),
                        )
                        audit_logs = [_map_audit_entry(r) for r in cur.fetchall()]
                        return ProvenanceHistoryResultDTO(
                            entity_type="taxon",
                            entity_id=entity_id,
                            current_state=current_state,
                            sources=self._fetch_sources_by_ids(cur, source_ids),
                            source_versions=self._fetch_source_versions_by_ids(cur, source_ids, sv_ids),
                            import_batches=import_batches,
                            import_records=import_records,
                            evidences=[],
                            review_events=[],
                            outbox_events=[],
                            dossier_versions=[],
                            audit_logs=audit_logs,
                        )

                    if normalized_type == "import_batch":
                        cur.execute(f"{_IMPORT_BATCH_SELECT} WHERE ib.import_batch_id = %s;", (entity_id,))
                        b_row = cur.fetchone()
                        if not b_row:
                            return None
                        batch_dto = _map_import_batch(b_row)
                        cur.execute(
                            f"{_IMPORT_RECORD_SELECT} WHERE ir.import_batch_id = %s ORDER BY ir.created_at ASC;",
                            (entity_id,),
                        )
                        rec_dtos = [_map_import_record(r) for r in cur.fetchall()]
                        cur.execute(
                            """
                            SELECT audit_id, entity_type, entity_id, action, performed_by, performed_at, details
                            FROM audit_log
                            WHERE entity_id = ANY(%s)
                            ORDER BY performed_at ASC, audit_id ASC;
                            """,
                            ([entity_id, *[r.import_record_id for r in rec_dtos]],),
                        )
                        audits = [_map_audit_entry(r) for r in cur.fetchall()]
                        sv_ids = [batch_dto.source_version_id] if batch_dto.source_version_id else []
                        return ProvenanceHistoryResultDTO(
                            entity_type="import_batch",
                            entity_id=entity_id,
                            current_state={"status": batch_dto.status, "received_count": batch_dto.received_count},
                            sources=self._fetch_sources_by_ids(cur, [batch_dto.source_id]),
                            source_versions=self._fetch_source_versions_by_ids(cur, [batch_dto.source_id], sv_ids),
                            import_batches=[batch_dto],
                            import_records=rec_dtos,
                            evidences=[],
                            review_events=[],
                            outbox_events=[],
                            dossier_versions=[],
                            audit_logs=audits,
                        )

                    if normalized_type == "import_record":
                        cur.execute(f"{_IMPORT_RECORD_SELECT} WHERE ir.import_record_id = %s;", (entity_id,))
                        r_row = cur.fetchone()
                        if not r_row:
                            return None
                        rec_dto = _map_import_record(r_row)
                        cur.execute(f"{_IMPORT_BATCH_SELECT} WHERE ib.import_batch_id = %s;", (rec_dto.import_batch_id,))
                        b_row = cur.fetchone()
                        batches = [_map_import_batch(b_row)] if b_row else []
                        src_ids = [b.source_id for b in batches]
                        sv_ids = [b.source_version_id for b in batches if b.source_version_id]
                        cur.execute(
                            """
                            SELECT audit_id, entity_type, entity_id, action, performed_by, performed_at, details
                            FROM audit_log
                            WHERE entity_id = ANY(%s)
                            ORDER BY performed_at ASC, audit_id ASC;
                            """,
                            ([entity_id, rec_dto.import_batch_id],),
                        )
                        audits = [_map_audit_entry(r) for r in cur.fetchall()]
                        return ProvenanceHistoryResultDTO(
                            entity_type="import_record",
                            entity_id=entity_id,
                            current_state={"validation_status": rec_dto.validation_status, "canonical_taxon_id": rec_dto.canonical_taxon_id},
                            sources=self._fetch_sources_by_ids(cur, src_ids),
                            source_versions=self._fetch_source_versions_by_ids(cur, src_ids, sv_ids),
                            import_batches=batches,
                            import_records=[rec_dto],
                            evidences=[],
                            review_events=[],
                            outbox_events=[],
                            dossier_versions=[],
                            audit_logs=audits,
                        )
        except psycopg.Error as exc:
            raise DatabaseUnavailableError("postgresql", str(exc)) from exc

        raise DomainValidationError(
            f"Tipo de entidad no soportado para historial de procedencia: '{entity_type}'.",
            details={"allowed_types": ["assertion", "taxon", "import_batch", "import_record"]},
        )
