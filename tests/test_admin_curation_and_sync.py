from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient
from neo4j.exceptions import ServiceUnavailable
from pymongo.errors import PyMongoError

from biolink_backend.api.dependencies import get_admin_curation_service, get_auth_service
from biolink_backend.application.dto import (
    AdminAssertionDetailDTO,
    AuditLogEntryDTO,
    CreateAssertionInputDTO,
    EvidenceDetailDTO,
    ImportBatchCreateInputDTO,
    ImportBatchDetailDTO,
    ImportBatchDTO,
    ImportRecordDTO,
    ImportRecordInputDTO,
    LocalitySummaryDTO,
    NormalizeImportRecordInputDTO,
    NormalizedImportRecordResultDTO,
    OutboxEventDTO,
    ProvenanceHistoryResultDTO,
    ReviewAssertionResultDTO,
    ReviewEventDTO,
    SourceSummaryDTO,
    SourceVersionDTO,
    TaxonDossierClaimDTO,
    TaxonDossierDTO,
    TaxonDossierVersionDTO,
    TaxonSummaryDTO,
    UpdateTaxonDossierInputDTO,
)
from biolink_backend.application.ports import StoredSessionRecord, StoredUserRecord
from biolink_backend.application.services.admin_curation_service import AdminCurationService
from biolink_backend.application.services.auth_service import AuthService
from biolink_backend.config.settings import Settings
from biolink_backend.domain.entities import Evidence, InteractionAssertion, OutboxEvent, Taxon
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
    DossierSyncStatus,
    EvidenceKind,
    EvidenceMethodCode,
    ImportRecordValidationStatus,
    InteractionTypeCode,
    OrganismGroup,
    OutboxEventType,
    OutboxStatus,
    ProjectionStatus,
    RoleCode,
    TaxonomicRank,
    TaxonomicStatus,
)
from biolink_backend.main import create_app
from biolink_backend.repositories.document.admin_dossier_repository import MongoAdminDossierRepository
from biolink_backend.repositories.graph.projection_repository import Neo4jProjectionRepository
from biolink_backend.repositories.relational.admin_curation_repository import PostgresAdminCurationRepository
from biolink_backend.repositories.relational.outbox_repository import PostgresOutboxRepository
from biolink_backend.security.passwords import hash_password
from biolink_backend.shared.exceptions import (
    BusinessRuleViolationError,
    DatabaseUnavailableError,
    DomainValidationError,
    EntityNotFoundError,
)
from biolink_backend.workers.outbox_worker import OutboxProjectionWorker
from tests.conftest import build_fake_health_service


# ----------------------------------------------------------------------------
# Almacen en memoria controlado que refleja las reglas ACID/Outbox reales
# ----------------------------------------------------------------------------


class InMemoryMultiModelHarness:
    def __init__(self) -> None:
        curator_hash = hash_password("CuratorTest123!", iterations=5_000)
        reviewer_hash = hash_password("ReviewerTest123!", iterations=5_000)
        self.users: dict[str, StoredUserRecord] = {
            "curador1@biolink.org": StoredUserRecord(
                user_id="USR_CURATOR_1",
                display_name="Curador Prueba",
                email="curador1@biolink.org",
                password_hash=curator_hash,
                is_active=True,
                roles=frozenset({RoleCode.CURATOR}),
            ),
            "revisora1@biolink.org": StoredUserRecord(
                user_id="USR_REVIEWER_1",
                display_name="Revisora Prueba",
                email="revisora1@biolink.org",
                password_hash=reviewer_hash,
                is_active=True,
                roles=frozenset({RoleCode.REVIEWER}),
            ),
        }
        self.sessions: dict[str, StoredSessionRecord] = {}
        self.taxa: dict[str, Taxon] = {
            "TAX_AN_001": Taxon(
                "TAX_AN_001", "Artibeus lituratus", "(Olfers, 1818)",
                TaxonomicRank.SPECIES, OrganismGroup.ANIMAL, TaxonomicStatus.ACCEPTED,
            ),
            "TAX_PL_003": Taxon(
                "TAX_PL_003", "Ficus insipida", "",
                TaxonomicRank.SPECIES, OrganismGroup.PLANT, TaxonomicStatus.ACCEPTED,
            ),
        }
        self.taxon_dossier_pg_state: dict[str, tuple[int, str]] = {
            "TAX_AN_001": (2, DossierSyncStatus.SYNCED.value),
            "TAX_PL_003": (0, DossierSyncStatus.SYNCED.value),
        }
        self.sources: dict[str, SourceSummaryDTO] = {
            "BAT2007": SourceSummaryDTO(
                source_id="BAT2007",
                source_type="PUBLICATION",
                title="Diversidad de frutos consumidos",
                authors="Olea-Wagner et al.",
                publication_year=2007,
                citation_text="Olea-Wagner et al. (2007)",
                license_id="ACADEMIC_CITATION_ONLY",
            )
        }
        self.source_versions: dict[str, SourceVersionDTO] = {}
        self.localities: dict[str, LocalitySummaryDTO] = {
            "LOC_RBMA_SOUTH": LocalitySummaryDTO(
                locality_id="LOC_RBMA_SOUTH",
                name="Montes Azules (sector sur)",
                locality_type="STUDY_AREA",
                protected_status="INSIDE_ANP",
                spatial_validation_status="VERIFIED_POLYGON",
                coordinate_precision="REGIONAL_STUDY_SECTOR",
                parent_locality_id=None,
            )
        }
        self.import_batches: dict[str, ImportBatchDTO] = {}
        self.import_records: dict[str, ImportRecordDTO] = {}
        self.raw_docs: dict[str, dict[str, Any]] = {}
        self.assertions: dict[str, AdminAssertionDetailDTO] = {}
        self.evidences: dict[str, list[EvidenceDetailDTO]] = {}
        self.review_events: list[ReviewEventDTO] = []
        self.outbox_events: list[OutboxEvent] = []
        self.audit_logs: list[AuditLogEntryDTO] = []
        self.neo4j_nodes: dict[str, dict[str, Any]] = {}
        self.neo4j_edges_count: dict[str, int] = {}
        self.neo4j_down: bool = False
        self.fail_pg_dossier_record: bool = False

        now = "2026-11-21T00:00:00+00:00"
        self.dossiers: dict[str, TaxonDossierDTO] = {
            "TAX_AN_001": TaxonDossierDTO(
                taxon_id="TAX_AN_001",
                schema_version=1,
                document_version=2,
                history_versions_count=1,
                summary_description="Resumen v2",
                summary_source_refs=["BAT2007"],
                summary_locators=["p. 195"],
                sections={"ecology": {"diet": ["Ficus"], "habitats": ["Selva"], "activity_patterns": [], "notes": []}},
                claims=[TaxonDossierClaimDTO("C1", "diet", "Ficus", "BAT2007", "p. 195")],
                media=[],
                updated_at=now,
            )
        }
        self.dossier_versions: dict[str, list[TaxonDossierVersionDTO]] = {
            "TAX_AN_001": [
                TaxonDossierVersionDTO(
                    version_id="TAX_AN_001::v1",
                    taxon_id="TAX_AN_001",
                    document_version=1,
                    archived_at="2026-11-20T00:00:00Z",
                    archived_by="USR_CURATOR_1",
                    change_reason="Inicial",
                    snapshot={"document_version": 1},
                )
            ]
        }
        self._seq = 0

    def next_id(self, prefix: str) -> str:
        self._seq += 1
        return f"{prefix}_{self._seq:04d}"

    def add_audit(self, entity_type: str, entity_id: str, action: str, user_id: str | None, details: dict[str, Any]) -> None:
        self._seq += 1
        self.audit_logs.append(
            AuditLogEntryDTO(
                audit_id=self._seq,
                entity_type=entity_type,
                entity_id=entity_id,
                action=action,
                performed_by=user_id,
                performed_at="2026-11-21T12:00:00+00:00",
                details=details,
            )
        )


class FakeAuthRepo:
    def __init__(self, h: InMemoryMultiModelHarness) -> None:
        self._h = h

    def get_user_by_email(self, email: str) -> StoredUserRecord | None:
        return self._h.users.get(email.lower())

    def create_session(self, *, session_id: str, user_id: str, token_hash: str, expires_at: datetime, **_kw) -> None:
        user = next(u for u in self._h.users.values() if u.user_id == user_id)
        self._h.sessions[token_hash] = StoredSessionRecord(
            session_id=session_id,
            user_id=user.user_id,
            email=user.email,
            display_name=user.display_name,
            is_active=user.is_active,
            roles=user.roles,
            expires_at=expires_at,
        )
        self._h.add_audit("session", session_id, "LOGIN", user_id, {})

    def get_active_session_by_token_hash(self, token_hash: str) -> StoredSessionRecord | None:
        return self._h.sessions.get(token_hash)

    def revoke_session_by_token_hash(self, token_hash: str) -> bool:
        sess = self._h.sessions.pop(token_hash, None)
        if sess is None:
            return False
        self._h.add_audit("session", sess.session_id, "LOGOUT", sess.user_id, {})
        return True


class FakeAdminDocRepo:
    def __init__(self, h: InMemoryMultiModelHarness) -> None:
        self._h = h

    def insert_raw_source_records(
        self,
        *,
        import_batch_id: str,
        source_id: str,
        provider: str,
        license_id: str,
        records: list[ImportRecordInputDTO],
    ) -> dict[str, str]:
        mapping: dict[str, str] = {}
        for r in records:
            doc_id = f"RAW_{import_batch_id}_{r.external_record_id}"
            self._h.raw_docs[doc_id] = {
                "_id": doc_id,
                "raw_payload": dict(r.raw_payload),
                "checksum": "a" * 64,
                "license_metadata": {"license_id": license_id, "redistribution_permitted": r.redistribution_permitted},
                "transformation_metadata": {"validation_status": "PENDING", "target_canonical_taxon_id": None},
            }
            mapping[r.external_record_id] = doc_id
        return mapping

    def update_raw_source_transformation(
        self,
        *,
        raw_document_id: str,
        target_canonical_taxon_id: str | None,
        validation_status: str,
        warnings: list[str] | None = None,
    ) -> bool:
        doc = self._h.raw_docs.get(raw_document_id)
        if not doc:
            return False
        doc["transformation_metadata"] = {
            "target_canonical_taxon_id": target_canonical_taxon_id,
            "validation_status": validation_status,
            "warnings": warnings or [],
        }
        return True

    def update_taxon_dossier_versioned(
        self,
        *,
        taxon_id: str,
        accepted_name: str,
        data: UpdateTaxonDossierInputDTO,
        performed_by: str,
    ) -> tuple[TaxonDossierDTO, int | None, str | None]:
        current = self._h.dossiers.get(taxon_id)
        prev_ver: int | None = None
        archived_id: str | None = None
        if current is not None:
            if data.expected_version is not None and data.expected_version != current.document_version:
                raise BusinessRuleViolationError("RD-15", "Conflicto optimista de version")
            prev_ver = current.document_version
            new_ver = prev_ver + 1
            archived_id = f"{taxon_id}::v{prev_ver}"
            self._h.dossier_versions.setdefault(taxon_id, []).append(
                TaxonDossierVersionDTO(
                    version_id=archived_id,
                    taxon_id=taxon_id,
                    document_version=prev_ver,
                    archived_at="2026-11-21T12:00:00Z",
                    archived_by=performed_by,
                    change_reason=data.change_reason,
                    snapshot={"document_version": prev_ver, "summary": current.summary_description},
                )
            )
        else:
            new_ver = 1

        history_count = len(self._h.dossier_versions.get(taxon_id, []))
        new_dto = TaxonDossierDTO(
            taxon_id=taxon_id,
            schema_version=1,
            document_version=new_ver,
            history_versions_count=history_count,
            summary_description=data.summary_description,
            summary_source_refs=list(data.summary_source_refs),
            summary_locators=list(data.summary_locators),
            sections=dict(data.sections),
            claims=[TaxonDossierClaimDTO(c.claim_id, c.field, c.value, c.source_id, c.source_locator) for c in data.claims],
            media=[],
            updated_at="2026-11-21T12:00:00Z",
        )
        self._h.dossiers[taxon_id] = new_dto
        return new_dto, prev_ver, archived_id

    def get_taxon_dossier_version_history(self, taxon_id: str) -> list[TaxonDossierVersionDTO]:
        return list(self._h.dossier_versions.get(taxon_id, []))

    def list_all_taxon_dossier_versions(self) -> dict[str, int]:
        return {tid: d.document_version for tid, d in self._h.dossiers.items()}


class FakeAdminRelRepo:
    def __init__(self, h: InMemoryMultiModelHarness) -> None:
        self._h = h

    def create_import_batch(
        self,
        *,
        data: ImportBatchCreateInputDTO,
        performed_by: str,
        raw_document_ids: dict[str, str],
        batch_id: str | None = None,
    ) -> ImportBatchDetailDTO:
        if data.auto_publish:
            raise BusinessRuleViolationError("RD-13", "No autopublicar")
        if data.source_id not in self._h.sources:
            raise EntityNotFoundError("source", data.source_id)
        bid = batch_id or self._h.next_id("BAT")
        sv_id = self._h.next_id("SVER")
        self._h.source_versions[sv_id] = SourceVersionDTO(
            source_version_id=sv_id,
            provider_version=data.provider_version,
            release_date=data.release_date,
            retrieved_at="2026-11-21T12:00:00+00:00",
            endpoint_or_file=data.endpoint_or_file,
            query_parameters=data.query_parameters,
            filters=data.filters,
            license_id=data.license_id or "ACADEMIC_CITATION_ONLY",
            checksum="a" * 64,
        )
        records: list[ImportRecordDTO] = []
        for r in data.records:
            rid = self._h.next_id("REC")
            rec_dto = ImportRecordDTO(
                import_record_id=rid,
                import_batch_id=bid,
                external_record_id=r.external_record_id,
                raw_document_id=raw_document_ids.get(r.external_record_id),
                validation_status="PENDING",
                canonical_taxon_id=None,
                canonical_accepted_name=None,
                verbatim_name=r.verbatim_name,
                proposed_external_scheme=r.proposed_external_scheme,
                proposed_external_id=r.proposed_external_id,
                matching_rule=None,
                validation_notes=None,
                normalized_by=None,
                normalized_at=None,
                created_at="2026-11-21T12:00:00+00:00",
            )
            self._h.import_records[rid] = rec_dto
            records.append(rec_dto)
        batch_dto = ImportBatchDTO(
            import_batch_id=bid,
            source_id=data.source_id,
            source_version_id=sv_id,
            provider_version=data.provider_version,
            endpoint_or_file=data.endpoint_or_file,
            started_by=performed_by,
            started_at="2026-11-21T12:00:00+00:00",
            finished_at="2026-11-21T12:00:00+00:00",
            query_parameters=data.query_parameters,
            filters=data.filters,
            status="COMPLETED",
            received_count=len(records),
            accepted_count=0,
            rejected_count=0,
            pending_count=len(records),
            unresolved_count=0,
        )
        self._h.import_batches[bid] = batch_dto
        self._h.add_audit("import_batch", bid, "INSERT", performed_by, {"received_count": len(records)})
        return ImportBatchDetailDTO(batch=batch_dto, records=records)

    def get_import_batch_detail(self, import_batch_id: str) -> ImportBatchDetailDTO | None:
        batch = self._h.import_batches.get(import_batch_id)
        if not batch:
            return None
        recs = [r for r in self._h.import_records.values() if r.import_batch_id == import_batch_id]
        return ImportBatchDetailDTO(batch=batch, records=recs)

    def list_import_records(
        self,
        *,
        import_batch_id: str | None = None,
        validation_status: str | None = None,
        limit: int = 100,
    ) -> list[ImportRecordDTO]:
        items = list(self._h.import_records.values())
        if import_batch_id:
            items = [i for i in items if i.import_batch_id == import_batch_id]
        if validation_status:
            items = [i for i in items if i.validation_status == validation_status]
        return items[:limit]

    def normalize_import_record(
        self,
        *,
        data: NormalizeImportRecordInputDTO,
        performed_by: str,
    ) -> tuple[NormalizedImportRecordResultDTO, str | None]:
        rec = self._h.import_records.get(data.import_record_id)
        if not rec:
            raise EntityNotFoundError("import_record", data.import_record_id)
        status_enum = ImportRecordValidationStatus(data.validation_status)
        if status_enum == ImportRecordValidationStatus.VALID and not data.canonical_taxon_id:
            raise DomainValidationError("VALID requiere canonical_taxon_id")
        accepted_name = None
        if data.canonical_taxon_id:
            if data.canonical_taxon_id not in self._h.taxa:
                raise EntityNotFoundError("taxon", data.canonical_taxon_id)
            accepted_name = self._h.taxa[data.canonical_taxon_id].accepted_name

        updated_rec = ImportRecordDTO(
            import_record_id=rec.import_record_id,
            import_batch_id=rec.import_batch_id,
            external_record_id=rec.external_record_id,
            raw_document_id=rec.raw_document_id,
            validation_status=status_enum.value,
            canonical_taxon_id=data.canonical_taxon_id,
            canonical_accepted_name=accepted_name,
            verbatim_name=data.verbatim_name or rec.verbatim_name,
            proposed_external_scheme=data.external_scheme or rec.proposed_external_scheme,
            proposed_external_id=data.external_id or rec.proposed_external_id,
            matching_rule=data.matching_rule,
            validation_notes=data.validation_notes,
            normalized_by=performed_by,
            normalized_at="2026-11-21T12:05:00+00:00",
            created_at=rec.created_at,
        )
        self._h.import_records[rec.import_record_id] = updated_rec
        batch_recs = [r for r in self._h.import_records.values() if r.import_batch_id == rec.import_batch_id]
        old_b = self._h.import_batches[rec.import_batch_id]
        updated_batch = ImportBatchDTO(
            import_batch_id=old_b.import_batch_id,
            source_id=old_b.source_id,
            source_version_id=old_b.source_version_id,
            provider_version=old_b.provider_version,
            endpoint_or_file=old_b.endpoint_or_file,
            started_by=old_b.started_by,
            started_at=old_b.started_at,
            finished_at=old_b.finished_at,
            query_parameters=old_b.query_parameters,
            filters=old_b.filters,
            status="PARTIAL" if any(r.validation_status in ("PENDING", "UNRESOLVED") for r in batch_recs) else "COMPLETED",
            received_count=len(batch_recs),
            accepted_count=sum(1 for r in batch_recs if r.validation_status == "VALID"),
            rejected_count=sum(1 for r in batch_recs if r.validation_status in ("INVALID", "DUPLICATE")),
            pending_count=sum(1 for r in batch_recs if r.validation_status == "PENDING"),
            unresolved_count=sum(1 for r in batch_recs if r.validation_status == "UNRESOLVED"),
        )
        self._h.import_batches[rec.import_batch_id] = updated_batch
        self._h.add_audit("import_record", rec.import_record_id, "UPDATE", performed_by, {"status": status_enum.value})
        return (
            NormalizedImportRecordResultDTO(
                record=updated_rec,
                batch=updated_batch,
                registered_name_id="NM_001" if status_enum == ImportRecordValidationStatus.VALID else None,
                registered_identifier_id="TID_001" if updated_rec.proposed_external_id else None,
            ),
            rec.raw_document_id,
        )

    def create_interaction_assertion(
        self, *, data: CreateAssertionInputDTO, performed_by: str
    ) -> AdminAssertionDetailDTO:
        initial = AssertionStatus(data.initial_status)
        if initial not in (AssertionStatus.DRAFT, AssertionStatus.IN_REVIEW):
            raise BusinessRuleViolationError("RD-13", "Solo DRAFT o IN_REVIEW")
        ensure_not_occurrence_only(bool(data.evidences))
        it_code = InteractionTypeCode(data.interaction_type_code)
        validate_interaction_type_for_v1(it_code)
        PartialDateRange(data.period_start, data.period_end)

        actor = self._h.taxa[data.actor_taxon_id]
        target = self._h.taxa[data.target_taxon_id]
        validate_v1_actor_target_groups(actor, target)
        loc = self._h.localities[data.locality_id]

        aid = self._h.next_id("AST")
        ev_list: list[EvidenceDetailDTO] = []
        for ev in data.evidences:
            eid = ev.evidence_id or self._h.next_id("EVD")
            src = self._h.sources[ev.source_id or "BAT2007"]
            ev_list.append(
                EvidenceDetailDTO(
                    evidence_id=eid,
                    source_id=src.source_id,
                    source_version_id=ev.source_version_id,
                    source_locator=ev.source_locator or "p. 195",
                    evidence_kind=ev.evidence_kind or "OBSERVATION",
                    method_code=ev.method_code or data.method_code,
                    reported_term=ev.reported_term or it_code.value,
                    summary=ev.summary or "Consumo",
                    interpretation_limit=ev.interpretation_limit or "No demuestra dispersion",
                    period_start=ev.period_start or data.period_start,
                    period_end=ev.period_end or data.period_end,
                    license_id=ev.license_id or src.license_id,
                    evidence_role=ev.evidence_role,
                    review_note=ev.review_note,
                    source=src,
                )
            )
        self._h.evidences[aid] = ev_list
        detail = AdminAssertionDetailDTO(
            assertion_id=aid,
            status=initial.value,
            aggregate_version=1,
            projection_status=ProjectionStatus.NOT_APPLICABLE.value,
            last_projected_at=None,
            interaction_type_code=it_code.value,
            actor_taxon=TaxonSummaryDTO(
                actor.taxon_id, actor.accepted_name, actor.scientific_authorship,
                actor.taxonomic_rank.value, actor.organism_group.value, actor.taxonomic_status.value,
            ),
            target_taxon=TaxonSummaryDTO(
                target.taxon_id, target.accepted_name, target.scientific_authorship,
                target.taxonomic_rank.value, target.organism_group.value, target.taxonomic_status.value,
            ),
            locality=loc,
            period_start=data.period_start,
            period_end=data.period_end,
            season_label=data.season_label,
            method_code=data.method_code,
            assertion_scope=data.assertion_scope,
            created_by=performed_by,
            created_at="2026-11-21T12:00:00+00:00",
            reviewed_by=None,
            reviewed_at=None,
            updated_at="2026-11-21T12:00:00+00:00",
            evidences=ev_list,
            graph_projected=False,
        )
        self._h.assertions[aid] = detail
        self._h.add_audit("interaction_assertion", aid, "INSERT", performed_by, {"status": initial.value})
        return detail

    def get_assertion_admin_detail(self, assertion_id: str) -> AdminAssertionDetailDTO | None:
        return self._h.assertions.get(assertion_id)

    def transition_assertion_status(
        self, *, assertion_id: str, new_status: str, reason: str, performed_by: str
    ) -> ReviewAssertionResultDTO:
        cur_detail = self._h.assertions.get(assertion_id)
        if not cur_detail:
            raise EntityNotFoundError("interaction_assertion", assertion_id)
        cur_status = AssertionStatus(cur_detail.status)
        tgt_status = AssertionStatus(new_status)
        validate_state_transition(cur_status, tgt_status)

        ev_list = self._h.evidences.get(assertion_id, [])
        domain_evs = [
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
            )
            for e in ev_list
        ]
        domain_ast = InteractionAssertion(
            assertion_id=cur_detail.assertion_id,
            actor_taxon_id=cur_detail.actor_taxon.taxon_id,
            target_taxon_id=cur_detail.target_taxon.taxon_id,
            interaction_type_code=InteractionTypeCode(cur_detail.interaction_type_code),
            locality_id=cur_detail.locality.locality_id,
            period=PartialDateRange(cur_detail.period_start, cur_detail.period_end),
            season_label=cur_detail.season_label,
            method_code=EvidenceMethodCode(cur_detail.method_code),
            assertion_scope=cur_detail.assertion_scope,
            status=cur_status,
            created_by=cur_detail.created_by,
            created_at=datetime.now(timezone.utc),
        )
        if tgt_status == AssertionStatus.PUBLISHED:
            validate_publication_requirements(domain_ast, domain_evs, performed_by)

        next_ver = cur_detail.aggregate_version + 1
        outbox_dto: OutboxEventDTO | None = None
        proj_status = ProjectionStatus.NOT_APPLICABLE.value
        outbox_type: OutboxEventType | None = None
        if tgt_status == AssertionStatus.PUBLISHED:
            outbox_type = OutboxEventType.ASSERTION_PUBLISHED
            proj_status = ProjectionStatus.PENDING.value
        elif cur_status == AssertionStatus.PUBLISHED and tgt_status == AssertionStatus.WITHDRAWN:
            outbox_type = OutboxEventType.ASSERTION_WITHDRAWN
            proj_status = ProjectionStatus.PENDING.value

        updated = AdminAssertionDetailDTO(
            assertion_id=cur_detail.assertion_id,
            status=tgt_status.value,
            aggregate_version=next_ver,
            projection_status=proj_status,
            last_projected_at=cur_detail.last_projected_at,
            interaction_type_code=cur_detail.interaction_type_code,
            actor_taxon=cur_detail.actor_taxon,
            target_taxon=cur_detail.target_taxon,
            locality=cur_detail.locality,
            period_start=cur_detail.period_start,
            period_end=cur_detail.period_end,
            season_label=cur_detail.season_label,
            method_code=cur_detail.method_code,
            assertion_scope=cur_detail.assertion_scope,
            created_by=cur_detail.created_by,
            created_at=cur_detail.created_at,
            reviewed_by=performed_by if tgt_status in (AssertionStatus.PUBLISHED, AssertionStatus.REJECTED, AssertionStatus.WITHDRAWN) else cur_detail.reviewed_by,
            reviewed_at="2026-11-21T12:10:00+00:00",
            updated_at="2026-11-21T12:10:00+00:00",
            evidences=ev_list,
            graph_projected=False,
        )
        self._h.assertions[assertion_id] = updated

        rev = ReviewEventDTO(
            review_event_id=self._h.next_id("REV"),
            assertion_id=assertion_id,
            user_id=performed_by,
            previous_status=cur_status.value,
            new_status=tgt_status.value,
            reason=reason,
            created_at="2026-11-21T12:10:00+00:00",
        )
        self._h.review_events.append(rev)

        if outbox_type is not None:
            obx_id = self._h.next_id("OBX")
            now_dt = datetime.now(timezone.utc)
            payload = {
                "event_id": obx_id,
                "event_type": outbox_type.value,
                "assertion_id": assertion_id,
                "aggregate_version": next_ver,
                "payload_version": 1,
                "actor_taxon_id": updated.actor_taxon.taxon_id,
                "actor_name": updated.actor_taxon.accepted_name,
                "target_taxon_id": updated.target_taxon.taxon_id,
                "target_name": updated.target_taxon.accepted_name,
                "interaction_type_code": updated.interaction_type_code,
                "locality_id": updated.locality.locality_id,
                "period_start": updated.period_start,
                "period_end": updated.period_end,
                "season_label": updated.season_label,
                "method_code": updated.method_code,
                "status": tgt_status.value,
                "evidence_ids": [e.evidence_id for e in ev_list],
                "source_id": ev_list[0].source_id if ev_list else "BAT2007",
            }
            ev_ent = OutboxEvent(
                outbox_event_id=obx_id,
                aggregate_type="InteractionAssertion",
                aggregate_id=assertion_id,
                event_type=outbox_type,
                aggregate_version=next_ver,
                payload_version=1,
                payload_json=payload,
                status=OutboxStatus.PENDING,
                attempts=0,
                created_at=now_dt,
            )
            self._h.outbox_events.append(ev_ent)
            outbox_dto = OutboxEventDTO(
                event_id=obx_id,
                event_type=outbox_type.value,
                assertion_id=assertion_id,
                aggregate_version=next_ver,
                payload_version=1,
                created_at=now_dt.isoformat(),
                processing_status=OutboxStatus.PENDING.value,
                attempts=0,
                last_error=None,
                processed_at=None,
                payload_json=payload,
            )

        self._h.add_audit("interaction_assertion", assertion_id, "UPDATE", performed_by, {"new_status": tgt_status.value})
        return ReviewAssertionResultDTO(
            assertion=updated,
            review_event=rev,
            outbox_event=outbox_dto,
            relational_committed=True,
            graph_sync_note="Outbox PENDING" if outbox_dto else "Sin outbox",
        )

    def ensure_taxon_and_sources_exist(self, taxon_id: str, source_ids: list[str]) -> TaxonSummaryDTO:
        if taxon_id not in self._h.taxa:
            raise EntityNotFoundError("taxon", taxon_id)
        for sid in source_ids:
            if sid not in self._h.sources:
                raise EntityNotFoundError("source", sid)
        t = self._h.taxa[taxon_id]
        return TaxonSummaryDTO(
            t.taxon_id, t.accepted_name, t.scientific_authorship,
            t.taxonomic_rank.value, t.organism_group.value, t.taxonomic_status.value,
        )

    def record_taxon_dossier_version(
        self, *, taxon_id: str, dossier_version: int, sync_status: str, performed_by: str, details: dict[str, Any]
    ) -> None:
        if self._h.fail_pg_dossier_record:
            raise DatabaseUnavailableError("postgresql", "simulated PG write failure after MongoDB commit")
        self._h.taxon_dossier_pg_state[taxon_id] = (dossier_version, sync_status)
        self._h.add_audit("taxon_dossier", taxon_id, "UPDATE", performed_by, details)

    def mark_taxon_dossier_divergence(
        self, *, taxon_id: str, sync_status: str, error_detail: str, performed_by: str
    ) -> None:
        ver, _ = self._h.taxon_dossier_pg_state.get(taxon_id, (0, sync_status))
        self._h.taxon_dossier_pg_state[taxon_id] = (ver, sync_status)
        self._h.add_audit("taxon_dossier", taxon_id, "SECURITY", performed_by, {"error": error_detail})

    def list_taxa_with_dossier_state(self) -> list[tuple[str, int, str]]:
        return [(tid, st[0], st[1]) for tid, st in self._h.taxon_dossier_pg_state.items()]

    def list_all_published_assertion_payloads(self) -> list[dict[str, Any]]:
        payloads: list[dict[str, Any]] = []
        for aid, ast in self._h.assertions.items():
            if ast.status == AssertionStatus.PUBLISHED.value:
                evs = self._h.evidences.get(aid, [])
                payloads.append(
                    {
                        "assertion_id": aid,
                        "actor_taxon_id": ast.actor_taxon.taxon_id,
                        "actor_name": ast.actor_taxon.accepted_name,
                        "target_taxon_id": ast.target_taxon.taxon_id,
                        "target_name": ast.target_taxon.accepted_name,
                        "interaction_type_code": ast.interaction_type_code,
                        "locality_id": ast.locality.locality_id,
                        "period_start": ast.period_start,
                        "period_end": ast.period_end,
                        "season_label": ast.season_label,
                        "method_code": ast.method_code,
                        "status": ast.status,
                        "aggregate_version": ast.aggregate_version,
                        "evidence_ids": [e.evidence_id for e in evs],
                        "source_id": evs[0].source_id if evs else "BAT2007",
                    }
                )
        return payloads

    def mark_assertions_projection_synced(self, assertion_ids: list[str]) -> int:
        cnt = 0
        for aid in assertion_ids:
            cur = self._h.assertions.get(aid)
            if cur:
                self._h.assertions[aid] = AdminAssertionDetailDTO(
                    **{**asdict(cur), "actor_taxon": cur.actor_taxon, "target_taxon": cur.target_taxon,
                       "locality": cur.locality, "evidences": cur.evidences,
                       "projection_status": "SYNCED", "graph_projected": cur.status == "PUBLISHED"}
                )
                cnt += 1
        return cnt

    def get_provenance_history(self, *, entity_type: str, entity_id: str) -> ProvenanceHistoryResultDTO | None:
        if entity_type == "assertion":
            ast = self._h.assertions.get(entity_id)
            if not ast:
                return None
            return ProvenanceHistoryResultDTO(
                entity_type="assertion",
                entity_id=entity_id,
                current_state={"status": ast.status, "projection_status": ast.projection_status},
                sources=[self._h.sources["BAT2007"]],
                source_versions=list(self._h.source_versions.values()),
                import_batches=list(self._h.import_batches.values()),
                import_records=list(self._h.import_records.values()),
                evidences=ast.evidences,
                review_events=[r for r in self._h.review_events if r.assertion_id == entity_id],
                outbox_events=[
                    OutboxEventDTO(
                        event_id=o.outbox_event_id,
                        event_type=o.event_type.value,
                        assertion_id=o.aggregate_id,
                        aggregate_version=o.aggregate_version,
                        payload_version=o.payload_version,
                        created_at=o.created_at.isoformat(),
                        processing_status=o.status.value,
                        attempts=o.attempts,
                        last_error=o.error_message,
                        processed_at=o.processed_at.isoformat() if o.processed_at else None,
                        payload_json=o.payload_json,
                    )
                    for o in self._h.outbox_events
                    if o.aggregate_id == entity_id
                ],
                dossier_versions=[],
                audit_logs=[a for a in self._h.audit_logs if a.entity_id == entity_id],
            )
        if entity_type == "taxon":
            t = self._h.taxa.get(entity_id)
            if not t:
                return None
            return ProvenanceHistoryResultDTO(
                entity_type="taxon",
                entity_id=entity_id,
                current_state={"taxon_id": entity_id, "accepted_name": t.accepted_name},
                sources=[self._h.sources["BAT2007"]],
                source_versions=[],
                import_batches=[],
                import_records=[r for r in self._h.import_records.values() if r.canonical_taxon_id == entity_id],
                evidences=[],
                review_events=[],
                outbox_events=[],
                dossier_versions=[],
                audit_logs=[a for a in self._h.audit_logs if a.entity_id == entity_id],
            )
        raise DomainValidationError(f"Tipo no soportado: {entity_type}")


class FakeOutboxRepo:
    def __init__(self, h: InMemoryMultiModelHarness) -> None:
        self._h = h

    def claim_pending_events(self, limit: int, *, max_attempts: int = 5) -> list[OutboxEvent]:
        claimed: list[OutboxEvent] = []
        for idx, ev in enumerate(self._h.outbox_events):
            if ev.status in (OutboxStatus.PENDING, OutboxStatus.FAILED) and ev.attempts < max_attempts:
                updated = OutboxEvent(
                    outbox_event_id=ev.outbox_event_id,
                    aggregate_type=ev.aggregate_type,
                    aggregate_id=ev.aggregate_id,
                    event_type=ev.event_type,
                    aggregate_version=ev.aggregate_version,
                    payload_version=ev.payload_version,
                    payload_json=ev.payload_json,
                    status=OutboxStatus.PROCESSING,
                    attempts=ev.attempts,
                    created_at=ev.created_at,
                    processed_at=ev.processed_at,
                    error_message=ev.error_message,
                )
                self._h.outbox_events[idx] = updated
                claimed.append(updated)
                if len(claimed) >= limit:
                    break
        return claimed

    def mark_processed(self, outbox_event_id: str) -> None:
        for idx, ev in enumerate(self._h.outbox_events):
            if ev.outbox_event_id == outbox_event_id:
                self._h.outbox_events[idx] = OutboxEvent(
                    outbox_event_id=ev.outbox_event_id,
                    aggregate_type=ev.aggregate_type,
                    aggregate_id=ev.aggregate_id,
                    event_type=ev.event_type,
                    aggregate_version=ev.aggregate_version,
                    payload_version=ev.payload_version,
                    payload_json=ev.payload_json,
                    status=OutboxStatus.PROCESSED,
                    attempts=ev.attempts,
                    created_at=ev.created_at,
                    processed_at=datetime.now(timezone.utc),
                    error_message=None,
                )
                cur = self._h.assertions.get(ev.aggregate_id)
                if cur:
                    self._h.assertions[ev.aggregate_id] = AdminAssertionDetailDTO(
                        **{**asdict(cur), "actor_taxon": cur.actor_taxon, "target_taxon": cur.target_taxon,
                           "locality": cur.locality, "evidences": cur.evidences,
                           "projection_status": "SYNCED",
                           "last_projected_at": "2026-11-21T12:15:00+00:00",
                           "graph_projected": cur.status == "PUBLISHED"}
                    )
                break

    def mark_failed(self, outbox_event_id: str, *, error_message: str, max_attempts: int = 5) -> None:
        for idx, ev in enumerate(self._h.outbox_events):
            if ev.outbox_event_id == outbox_event_id:
                next_attempts = ev.attempts + 1
                new_st = OutboxStatus.DEAD_LETTER if next_attempts >= max_attempts else OutboxStatus.FAILED
                self._h.outbox_events[idx] = OutboxEvent(
                    outbox_event_id=ev.outbox_event_id,
                    aggregate_type=ev.aggregate_type,
                    aggregate_id=ev.aggregate_id,
                    event_type=ev.event_type,
                    aggregate_version=ev.aggregate_version,
                    payload_version=ev.payload_version,
                    payload_json=ev.payload_json,
                    status=new_st,
                    attempts=next_attempts,
                    created_at=ev.created_at,
                    processed_at=None,
                    error_message=error_message,
                )
                cur = self._h.assertions.get(ev.aggregate_id)
                if cur:
                    self._h.assertions[ev.aggregate_id] = AdminAssertionDetailDTO(
                        **{**asdict(cur), "actor_taxon": cur.actor_taxon, "target_taxon": cur.target_taxon,
                           "locality": cur.locality, "evidences": cur.evidences,
                           "projection_status": "FAILED", "graph_projected": False}
                    )
                break

    def reset_failed_and_dead_letter_events(self, assertion_id: str | None = None) -> int:
        cnt = 0
        for idx, ev in enumerate(self._h.outbox_events):
            if ev.status in (OutboxStatus.FAILED, OutboxStatus.DEAD_LETTER) and (
                assertion_id is None or ev.aggregate_id == assertion_id
            ):
                self._h.outbox_events[idx] = OutboxEvent(
                    outbox_event_id=ev.outbox_event_id,
                    aggregate_type=ev.aggregate_type,
                    aggregate_id=ev.aggregate_id,
                    event_type=ev.event_type,
                    aggregate_version=ev.aggregate_version,
                    payload_version=ev.payload_version,
                    payload_json=ev.payload_json,
                    status=OutboxStatus.PENDING,
                    attempts=0,
                    created_at=ev.created_at,
                    processed_at=None,
                    error_message=None,
                )
                cnt += 1
        return cnt

    def list_events_for_aggregate(self, aggregate_id: str) -> list[OutboxEventDTO]:
        return []


class FakeGraphProjectionRepo:
    def __init__(self, h: InMemoryMultiModelHarness) -> None:
        self._h = h

    def project_assertion(self, payload: dict[str, Any]) -> str:
        if self._h.neo4j_down:
            raise DatabaseUnavailableError("neo4j", "Simulated Neo4j outage")
        aid = str(payload["assertion_id"])
        # MERGE idempotente: no crea nodos duplicados ante replay
        self._h.neo4j_nodes[aid] = dict(payload)
        self._h.neo4j_edges_count[aid] = 1
        return aid

    def withdraw_assertion(self, assertion_id: str) -> int:
        if self._h.neo4j_down:
            raise DatabaseUnavailableError("neo4j", "Simulated Neo4j outage")
        self._h.neo4j_edges_count.pop(assertion_id, None)
        return 1 if self._h.neo4j_nodes.pop(assertion_id, None) else 0

    def withdraw_assertions_batch(self, assertion_ids: list[str]) -> int:
        return sum(self.withdraw_assertion(aid) for aid in assertion_ids)

    def list_projected_assertion_ids(self) -> set[str]:
        return set(self._h.neo4j_nodes.keys())

    def reconcile_published_assertions(self, expected_payloads: list[dict[str, Any]]) -> dict[str, int]:
        if self._h.neo4j_down:
            raise DatabaseUnavailableError("neo4j", "Simulated Neo4j outage")
        expected_ids = {str(p["assertion_id"]) for p in expected_payloads}
        stale = [aid for aid in list(self._h.neo4j_nodes) if aid not in expected_ids]
        removed = self.withdraw_assertions_batch(stale)
        for p in expected_payloads:
            self.project_assertion(p)
        return {"projected_or_verified": len(expected_payloads), "removed_stale": removed}


@contextmanager
def _build_api_client(settings: Settings):
    h = InMemoryMultiModelHarness()
    auth_svc = AuthService(repository=FakeAuthRepo(h), settings=settings)
    admin_svc = AdminCurationService(
        admin_relational=FakeAdminRelRepo(h),
        admin_document=FakeAdminDocRepo(h),
        outbox_repo=FakeOutboxRepo(h),
        graph_projection=FakeGraphProjectionRepo(h),
    )
    app = create_app(settings, health_service=build_fake_health_service(settings))
    app.dependency_overrides[get_auth_service] = lambda: auth_svc
    app.dependency_overrides[get_admin_curation_service] = lambda: admin_svc
    with TestClient(app) as client:
        yield client, h


def _login(client: TestClient, email: str, password: str) -> str:
    resp = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200
    return str(resp.json()["access_token"])


# ----------------------------------------------------------------------------
# Escenarios 1..10 de Flujo Administrativo, RBAC, Outbox y Reconciliacion
# ----------------------------------------------------------------------------


def test_rbac_unauthenticated_and_curator_cannot_approve(test_settings: Settings) -> None:
    with _build_api_client(test_settings) as (client, h):
        # 1. Usuario sin sesion rechazado (401)
        openapi_paths = set(client.get("/api/v1/openapi.json").json()["paths"].keys())
        expected_admin_routes = {
            "/api/v1/auth/login",
            "/api/v1/auth/logout",
            "/api/v1/auth/me",
            "/api/v1/admin/import-batches",
            "/api/v1/admin/import-batches/{import_batch_id}",
            "/api/v1/admin/import-records",
            "/api/v1/admin/import-records/{import_record_id}/normalize",
            "/api/v1/admin/assertions",
            "/api/v1/admin/assertions/{assertion_id}",
            "/api/v1/admin/assertions/{assertion_id}/submit",
            "/api/v1/admin/assertions/{assertion_id}/review",
            "/api/v1/admin/taxa/{taxon_id}/dossier",
            "/api/v1/admin/provenance/{entity_type}/{entity_id}",
            "/api/v1/admin/reconcile",
        }
        assert expected_admin_routes.issubset(openapi_paths)

        no_auth = client.post(
            "/api/v1/admin/assertions/AST_1/review",
            json={"action": "APPROVE", "reason": "Intento sin sesion"},
        )
        assert no_auth.status_code == 401

        cur_token = _login(client, "curador1@biolink.org", "CuratorTest123!")
        rev_token = _login(client, "revisora1@biolink.org", "ReviewerTest123!")

        # 2. Curador crea afirmacion en DRAFT y la envia a IN_REVIEW (OP-11)
        create_resp = client.post(
            "/api/v1/admin/assertions",
            headers={"Authorization": f"Bearer {cur_token}"},
            json={
                "actor_taxon_id": "TAX_AN_001",
                "target_taxon_id": "TAX_PL_003",
                "interaction_type_code": "CONSUMES_FRUIT_OF",
                "locality_id": "LOC_RBMA_SOUTH",
                "period_start": "2004-01",
                "period_end": "2004-07",
                "season_label": "Jan-Jul 2004",
                "method_code": "FECAL_SEED_IDENTIFICATION",
                "initial_status": "DRAFT",
                "evidences": [
                    {
                        "source_id": "BAT2007",
                        "source_locator": "Table 2, p. 196",
                        "summary": "Seeds identified in feces",
                        "interpretation_limit": "Does not prove seedling establishment",
                    }
                ],
            },
        )
        assert create_resp.status_code == 201
        aid = create_resp.json()["assertion_id"]
        assert create_resp.json()["status"] == "DRAFT"

        sub_resp = client.post(
            f"/api/v1/admin/assertions/{aid}/submit",
            headers={"Authorization": f"Bearer {cur_token}"},
            json={"reason": "Lista para revision editorial"},
        )
        assert sub_resp.status_code == 200
        assert sub_resp.json()["assertion"]["status"] == "IN_REVIEW"

        # Curador intentando aprobar -> 403 AUTHORIZATION_DENIED
        cur_approve = client.post(
            f"/api/v1/admin/assertions/{aid}/review",
            headers={"Authorization": f"Bearer {cur_token}"},
            json={"action": "APPROVE", "reason": "Autoaprobacion prohibida"},
        )
        assert cur_approve.status_code == 403
        assert cur_approve.json()["error"]["code"] == "AUTHORIZATION_DENIED"

        # Revisor intentando operacion exclusiva de Curador (OP-13 / OP-09) -> 403
        rev_import = client.post(
            "/api/v1/admin/import-batches",
            headers={"Authorization": f"Bearer {rev_token}"},
            json={
                "source_id": "BAT2007",
                "provider_version": "v1",
                "endpoint_or_file": "dataset.csv",
                "provider": "LITERATURE",
                "records": [{"external_record_id": "R1", "raw_payload": {"sp": "Artibeus"}}],
            },
        )
        assert rev_import.status_code == 403


def test_publication_without_evidence_rejected_rd02(test_settings: Settings) -> None:
    with _build_api_client(test_settings) as (client, h):
        cur_token = _login(client, "curador1@biolink.org", "CuratorTest123!")
        rev_token = _login(client, "revisora1@biolink.org", "ReviewerTest123!")

        # Regla RD-02 en creacion (OP-11 sin evidencias -> 422 por Pydantic / RD-01 directo en dominio)
        empty_ev = client.post(
            "/api/v1/admin/assertions",
            headers={"Authorization": f"Bearer {cur_token}"},
            json={
                "actor_taxon_id": "TAX_AN_001",
                "target_taxon_id": "TAX_PL_003",
                "interaction_type_code": "CONSUMES_FRUIT_OF",
                "locality_id": "LOC_RBMA_SOUTH",
                "period_start": "2004-01",
                "period_end": "2004-07",
                "season_label": "2004",
                "method_code": "FECAL_SEED_IDENTIFICATION",
                "evidences": [],
            },
        )
        assert empty_ev.status_code == 422

        # Simular afirmacion en IN_REVIEW cuyas evidencias fueron retiradas antes de aprobar
        h.assertions["AST_NO_EVIDENCE"] = AdminAssertionDetailDTO(
            assertion_id="AST_NO_EVIDENCE",
            status="IN_REVIEW",
            aggregate_version=1,
            projection_status="NOT_APPLICABLE",
            last_projected_at=None,
            interaction_type_code="CONSUMES_FRUIT_OF",
            actor_taxon=TaxonSummaryDTO("TAX_AN_001", "Artibeus lituratus", "", "SPECIES", "ANIMAL", "ACCEPTED"),
            target_taxon=TaxonSummaryDTO("TAX_PL_003", "Ficus insipida", "", "SPECIES", "PLANT", "ACCEPTED"),
            locality=h.localities["LOC_RBMA_SOUTH"],
            period_start="2004-01",
            period_end="2004-07",
            season_label="2004",
            method_code="FECAL_SEED_IDENTIFICATION",
            assertion_scope="BIOLINK_V1",
            created_by="USR_CURATOR_1",
            created_at="2026-11-21T00:00:00+00:00",
            reviewed_by=None,
            reviewed_at=None,
            updated_at="2026-11-21T00:00:00+00:00",
            evidences=[],
            graph_projected=False,
        )
        h.evidences["AST_NO_EVIDENCE"] = []

        resp = client.post(
            "/api/v1/admin/assertions/AST_NO_EVIDENCE/review",
            headers={"Authorization": f"Bearer {rev_token}"},
            json={"action": "APPROVE", "reason": "Intento sin evidencia"},
        )
        assert resp.status_code == 409
        assert resp.json()["error"]["code"] == "RULE_VIOLATION_RD_02"
        assert len(h.outbox_events) == 0


def test_reviewer_approval_outbox_neo4j_outage_retry_idempotency_and_withdrawal(test_settings: Settings) -> None:
    with _build_api_client(test_settings) as (client, h):
        cur_token = _login(client, "curador1@biolink.org", "CuratorTest123!")
        rev_token = _login(client, "revisora1@biolink.org", "ReviewerTest123!")

        created = client.post(
            "/api/v1/admin/assertions",
            headers={"Authorization": f"Bearer {cur_token}"},
            json={
                "actor_taxon_id": "TAX_AN_001",
                "target_taxon_id": "TAX_PL_003",
                "interaction_type_code": "CONSUMES_FRUIT_OF",
                "locality_id": "LOC_RBMA_SOUTH",
                "period_start": "2004-01",
                "period_end": "2004-07",
                "season_label": "Jan-Jul 2004",
                "method_code": "FECAL_SEED_IDENTIFICATION",
                "initial_status": "IN_REVIEW",
                "evidences": [
                    {
                        "source_id": "BAT2007",
                        "source_locator": "Table 2",
                        "summary": "Frugivory record",
                        "interpretation_limit": "Not dispersal",
                    }
                ],
            },
        )
        assert created.status_code == 201
        aid = created.json()["assertion_id"]

        # Simular Neo4j caido en el momento en que el Revisor aprueba (OP-12)
        h.neo4j_down = True
        approve_resp = client.post(
            f"/api/v1/admin/assertions/{aid}/review",
            headers={"Authorization": f"Bearer {rev_token}"},
            json={"action": "APPROVE", "reason": "Evidencia verificada en fuente primaria"},
        )
        assert approve_resp.status_code == 200
        body = approve_resp.json()
        # Publicacion relacional confirmada, proyeccion honesta como PENDING
        assert body["relational_committed"] is True
        assert body["assertion"]["status"] == "PUBLISHED"
        assert body["assertion"]["projection_status"] == "PENDING"
        assert body["assertion"]["graph_projected"] is False
        assert body["outbox_event"]["event_type"] == "ASSERTION_PUBLISHED"
        assert body["outbox_event"]["processing_status"] == "PENDING"
        assert body["outbox_event"]["aggregate_version"] == 2
        assert body["outbox_event"]["payload_version"] == 1
        assert body["outbox_event"]["attempts"] == 0
        assert body["outbox_event"]["last_error"] is None
        assert aid not in h.neo4j_nodes

        worker = OutboxProjectionWorker(
            outbox_repo=FakeOutboxRepo(h),
            graph_repo=FakeGraphProjectionRepo(h),
            max_attempts=3,
        )

        # Intento 1 con Neo4j caido -> evento pasa a FAILED, attempts=1, projection_status=FAILED
        proc1, fail1 = worker.process_once_detailed()
        assert proc1 == 0 and fail1 == 1
        assert h.outbox_events[0].status == OutboxStatus.FAILED
        assert h.outbox_events[0].attempts == 1
        assert "Simulated Neo4j outage" in (h.outbox_events[0].last_error or "")
        assert h.assertions[aid].projection_status == "FAILED"

        # Recuperacion de Neo4j -> siguiente ciclo reintenta y proyecta correctamente
        h.neo4j_down = False
        proc2, fail2 = worker.process_once_detailed()
        assert proc2 == 1 and fail2 == 0
        assert h.outbox_events[0].status == OutboxStatus.PROCESSED
        assert aid in h.neo4j_nodes
        assert h.assertions[aid].projection_status == "SYNCED"

        # Idempotencia ante evento duplicado intra-lote o replay
        dup_event = OutboxEvent(
            outbox_event_id="OBX_DUP_999",
            aggregate_type="InteractionAssertion",
            aggregate_id=aid,
            event_type=OutboxEventType.ASSERTION_PUBLISHED,
            aggregate_version=2,
            payload_version=1,
            payload_json=h.outbox_events[0].payload_json,
            status=OutboxStatus.PENDING,
            attempts=0,
            created_at=datetime.now(timezone.utc),
        )
        h.outbox_events.append(dup_event)
        assert worker.process_once() == 1
        assert h.neo4j_edges_count[aid] == 1

        # Retiro de afirmacion (PUBLISHED -> WITHDRAWN) elimina proyeccion en Neo4j
        withdraw_resp = client.post(
            f"/api/v1/admin/assertions/{aid}/review",
            headers={"Authorization": f"Bearer {rev_token}"},
            json={"action": "WITHDRAW", "reason": "Correccion taxonomica posterior"},
        )
        assert withdraw_resp.status_code == 200
        assert withdraw_resp.json()["assertion"]["status"] == "WITHDRAWN"
        assert withdraw_resp.json()["outbox_event"]["event_type"] == "ASSERTION_WITHDRAWN"

        assert worker.process_once() == 1
        assert aid not in h.neo4j_nodes
        assert h.assertions[aid].status == "WITHDRAWN"
        assert h.assertions[aid].projection_status == "SYNCED"


def test_import_normalization_dossier_versioning_reconciliation_and_provenance(test_settings: Settings) -> None:
    with _build_api_client(test_settings) as (client, h):
        cur_token = _login(client, "curador1@biolink.org", "CuratorTest123!")
        rev_token = _login(client, "revisora1@biolink.org", "ReviewerTest123!")

        # OP-09: Lote de importacion con preservacion inmutable y cero autopublicacion
        batch_resp = client.post(
            "/api/v1/admin/import-batches",
            headers={"Authorization": f"Bearer {cur_token}"},
            json={
                "source_id": "BAT2007",
                "provider_version": "gbif-2026-10",
                "endpoint_or_file": "exports/lacandona_bats.json",
                "provider": "GBIF",
                "query_parameters": {"taxonKey": 2432943},
                "filters": {"country": "MX"},
                "records": [
                    {
                        "external_record_id": "GBIF_1001",
                        "verbatim_name": "Artibeus lituratus palmarum",
                        "proposed_external_scheme": "GBIF",
                        "proposed_external_id": "2432943",
                        "raw_payload": {"scientificName": "Artibeus lituratus palmarum"},
                    },
                    {
                        "external_record_id": "GBIF_1002",
                        "verbatim_name": "Chiroptera sp. indet.",
                        "raw_payload": {"scientificName": "Chiroptera sp. indet."},
                    },
                ],
            },
        )
        assert batch_resp.status_code == 201
        recs = batch_resp.json()["records"]
        assert all(r["validation_status"] == "PENDING" for r in recs)

        # OP-10: Uno normalizado VALID y otro UNRESOLVED disponible para revision
        norm1 = client.patch(
            f"/api/v1/admin/import-records/{recs[0]['import_record_id']}/normalize",
            headers={"Authorization": f"Bearer {cur_token}"},
            json={
                "validation_status": "VALID",
                "canonical_taxon_id": "TAX_AN_001",
                "matching_rule": "TRINOMIAL_TO_CANONICAL_SPECIES",
                "validation_notes": "Subespecie sinonimizada a especie canonica",
            },
        )
        assert norm1.status_code == 200
        assert norm1.json()["record"]["canonical_taxon_id"] == "TAX_AN_001"
        assert norm1.json()["raw_document_updated"] is True

        norm2 = client.patch(
            f"/api/v1/admin/import-records/{recs[1]['import_record_id']}/normalize",
            headers={"Authorization": f"Bearer {cur_token}"},
            json={
                "validation_status": "UNRESOLVED",
                "matching_rule": "PENDING_MORPHOLOGY",
                "validation_notes": "Requiere revision de ejemplar",
            },
        )
        assert norm2.status_code == 200
        assert norm2.json()["batch"]["status"] == "PARTIAL"

        unresolved = client.get(
            "/api/v1/admin/import-records",
            params={"validation_status": "UNRESOLVED"},
            headers={"Authorization": f"Bearer {cur_token}"},
        )
        assert unresolved.status_code == 200
        assert len(unresolved.json()) == 1
        assert unresolved.json()[0]["external_record_id"] == "GBIF_1002"

        # OP-13: Actualizacion versionada preservando version previa y reconciliando divergencia PG/Mongo
        h.fail_pg_dossier_record = True
        upd_resp = client.put(
            "/api/v1/admin/taxa/TAX_AN_001/dossier",
            headers={"Authorization": f"Bearer {cur_token}"},
            json={
                "summary_description": "Resumen v3 con nueva evidencia de dieta.",
                "summary_source_refs": ["BAT2007"],
                "summary_locators": ["p. 197"],
                "sections": {"ecology": {"diet": ["Ficus", "Cecropia"]}},
                "claims": [
                    {
                        "claim_id": "C2",
                        "field": "ecology.diet",
                        "value": "Cecropia obtusifolia",
                        "source_id": "BAT2007",
                        "source_locator": "Table 2",
                    }
                ],
                "change_reason": "Incorporar Cecropia obtusifolia",
                "expected_version": 2,
            },
        )
        assert upd_resp.status_code == 200
        upd_body = upd_resp.json()
        assert upd_body["previous_version"] == 2
        assert upd_body["archived_version_id"] == "TAX_AN_001::v2"
        assert upd_body["dossier"]["document_version"] == 3
        assert upd_body["dossier_sync_status"] == "PENDING_RECONCILIATION"
        assert upd_body["sync_warning"] is not None

        # Verificar conflicto de version optimista -> 409
        conflict = client.put(
            "/api/v1/admin/taxa/TAX_AN_001/dossier",
            headers={"Authorization": f"Bearer {cur_token}"},
            json={
                "summary_description": "Conflicto obsoleto",
                "summary_source_refs": ["BAT2007"],
                "sections": {},
                "claims": [],
                "change_reason": "Version obsoleta",
                "expected_version": 2,
            },
        )
        assert conflict.status_code == 409

        # Reconciliacion tras fallo: restaura consistencia PG <-> MongoDB
        h.fail_pg_dossier_record = False
        rec_resp = client.post(
            "/api/v1/admin/reconcile",
            headers={"Authorization": f"Bearer {rev_token}"},
        )
        assert rec_resp.status_code == 200
        assert "TAX_AN_001" in rec_resp.json()["reconciled_taxon_ids"]
        assert h.taxon_dossier_pg_state["TAX_AN_001"] == (3, "SYNCED")

        # OP-14: Historial de procedencia del taxon incluye versiones documentales e importacion
        prov = client.get(
            "/api/v1/admin/provenance/taxon/TAX_AN_001",
            headers={"Authorization": f"Bearer {rev_token}"},
        )
        assert prov.status_code == 200
        assert len(prov.json()["dossier_versions"]) == 2
        assert prov.json()["dossier_versions"][1]["version_id"] == "TAX_AN_001::v2"
        assert len(prov.json()["import_records"]) == 1


# ----------------------------------------------------------------------------
# Pruebas unitarias directas de repositorios nativos (SQL / PyMongo / Cypher)
# ----------------------------------------------------------------------------


def test_native_repositories_sql_pymongo_and_cypher_behaviors() -> None:
    # 1. PostgreSQL Outbox SQL & Error mapping
    class _FakeCursor:
        def __init__(self) -> None:
            self.executed: list[tuple[str, Any]] = []
            self.rowcount = 1

        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

        def execute(self, sql: str, params: Any = None) -> None:
            self.executed.append((sql, params))

        def fetchall(self):
            return []

        def fetchone(self):
            return {"aggregate_type": "InteractionAssertion", "aggregate_id": "AST_1", "aggregate_version": 2}

    class _FakeTx:
        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

    class _FakeConn:
        def __init__(self, cur: _FakeCursor) -> None:
            self._cur = cur

        def cursor(self):
            return self._cur

        def transaction(self):
            return _FakeTx()

    class _FakePg:
        def __init__(self, cur: _FakeCursor | None = None, exc: Exception | None = None) -> None:
            self._cur = cur or _FakeCursor()
            self._exc = exc

        @contextmanager
        def connection(self):
            if self._exc:
                raise self._exc
            yield _FakeConn(self._cur)

    cur = _FakeCursor()
    obx_repo = PostgresOutboxRepository(_FakePg(cur))  # type: ignore[arg-type]
    obx_repo.claim_pending_events(10, max_attempts=5)
    assert "FOR UPDATE SKIP LOCKED" in cur.executed[0][0]
    obx_repo.mark_processed("OBX_1")
    assert any("projection_status = 'SYNCED'" in sql for sql, _ in cur.executed)
    obx_repo.mark_failed("OBX_1", error_message="Neo4j timeout", max_attempts=5)
    assert any("projection_status = 'FAILED'" in sql for sql, _ in cur.executed)

    bad_admin = PostgresAdminCurationRepository(_FakePg(exc=psycopg.OperationalError("down")))  # type: ignore[arg-type]
    with pytest.raises(DatabaseUnavailableError):
        bad_admin.get_import_batch_detail("BAT_1")

    # 2. MongoDB Dossier Repo: immutability of raw_payload + non-destructive version archive
    class _InMemMongoCollection:
        def __init__(self) -> None:
            self.docs: dict[str, dict[str, Any]] = {}

        def find_one(self, query: dict[str, Any]) -> dict[str, Any] | None:
            return self.docs.get(query["_id"])

        def insert_one(self, doc: dict[str, Any]) -> None:
            self.docs[doc["_id"]] = dict(doc)

        def replace_one(self, flt: dict[str, Any], doc: dict[str, Any], upsert: bool = False):
            doc_id = flt["_id"]
            existing = self.docs.get(doc_id)
            matched = 0
            if existing is not None:
                if "document_version" not in flt or existing.get("document_version") == flt["document_version"]:
                    self.docs[doc_id] = dict(doc)
                    matched = 1
            elif upsert:
                self.docs[doc_id] = dict(doc)
                matched = 1
            return type("Res", (), {"matched_count": matched})()

        def update_one(self, flt: dict[str, Any], upd: dict[str, Any]):
            doc_id = flt["_id"]
            existing = self.docs.get(doc_id)
            if not existing:
                return type("Res", (), {"matched_count": 0})()
            for k, v in upd.get("$set", {}).items():
                existing[k] = v
            return type("Res", (), {"matched_count": 1})()

        def count_documents(self, flt: dict[str, Any]) -> int:
            return sum(1 for d in self.docs.values() if d.get("taxon_id") == flt.get("taxon_id"))

        def find(self, flt: dict[str, Any], *_a):
            items = [d for d in self.docs.values() if not flt or d.get("taxon_id") == flt.get("taxon_id")]
            return type("Cur", (), {"sort": lambda _s, _k, _d=1: sorted(items, key=lambda x: x.get(_k, 0))})()

    db = {
        "raw_source_record": _InMemMongoCollection(),
        "taxon_dossier": _InMemMongoCollection(),
        "taxon_dossier_versions": _InMemMongoCollection(),
    }

    class _FakeMongoProv:
        def get_database(self):
            return db

    doc_repo = MongoAdminDossierRepository(_FakeMongoProv())  # type: ignore[arg-type]
    raw_map = doc_repo.insert_raw_source_records(
        import_batch_id="BAT_1",
        source_id="BAT2007",
        provider="GBIF",
        license_id="ACADEMIC_CITATION_ONLY",
        records=[ImportRecordInputDTO(external_record_id="EXT_1", raw_payload={"k": "v"})],
    )
    orig_checksum = db["raw_source_record"].docs[raw_map["EXT_1"]]["checksum"]
    doc_repo.update_raw_source_transformation(
        raw_document_id=raw_map["EXT_1"],
        target_canonical_taxon_id="TAX_AN_001",
        validation_status="VALID",
    )
    stored_raw = db["raw_source_record"].docs[raw_map["EXT_1"]]
    assert stored_raw["raw_payload"] == {"k": "v"}
    assert stored_raw["checksum"] == orig_checksum
    assert stored_raw["transformation_metadata"]["validation_status"] == "VALID"

    # 3. Neo4j Projection Repo Cypher & Error Mapping
    class _FakeNeoSess:
        def __init__(self) -> None:
            self.cyphers: list[str] = []

        def run(self, cypher: str, _params: dict[str, Any] | None = None):
            self.cyphers.append(cypher)
            return type(
                "R",
                (),
                {
                    "single": lambda _s: {"projected_assertion_id": "AST_1", "deleted_count": 1},
                    "consume": lambda _s: type("Sum", (), {"counters": type("C", (), {"nodes_deleted": 1})()})(),
                    "__iter__": lambda _s: iter([{"assertion_id": "AST_STALE"}]),
                },
            )()

    neo_sess = _FakeNeoSess()

    class _FakeNeoProv:
        def __init__(self, fail: bool = False) -> None:
            self._fail = fail

        @contextmanager
        def session(self):
            if self._fail:
                raise ServiceUnavailable("bolt closed")
            yield neo_sess

    proj = Neo4jProjectionRepository(_FakeNeoProv())  # type: ignore[arg-type]
    stats = proj.reconcile_published_assertions(
        [
            {
                "assertion_id": "AST_1",
                "actor_taxon_id": "TAX_AN_001",
                "target_taxon_id": "TAX_PL_003",
                "interaction_type_code": "CONSUMES_FRUIT_OF",
                "locality_id": "LOC_RBMA_SOUTH",
                "period_start": "2004-01",
                "period_end": "2004-07",
                "method_code": "FECAL_SEED_IDENTIFICATION",
                "status": "PUBLISHED",
                "evidence_ids": ["EVD_1"],
                "source_id": "BAT2007",
                "aggregate_version": 2,
            }
        ]
    )
    assert stats == {"projected_or_verified": 1, "removed_stale": 1}
    assert any("IN TRANSACTIONS OF 1000 ROWS" in c for c in neo_sess.cyphers)
    assert any("aggregate_version" in c for c in neo_sess.cyphers)

    with pytest.raises(DatabaseUnavailableError):
        Neo4jProjectionRepository(_FakeNeoProv(fail=True)).list_projected_assertion_ids()  # type: ignore[arg-type]
