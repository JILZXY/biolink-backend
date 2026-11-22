from __future__ import annotations

from dataclasses import replace

from biolink_backend.application.dto import (
    AdminAssertionDetailDTO,
    CreateAssertionInputDTO,
    ImportBatchCreateInputDTO,
    ImportBatchDetailDTO,
    ImportRecordDTO,
    NormalizeImportRecordInputDTO,
    NormalizedImportRecordResultDTO,
    ProvenanceHistoryResultDTO,
    ReconciliationReportDTO,
    ReviewAssertionResultDTO,
    UpdateTaxonDossierInputDTO,
    UpdateTaxonDossierResultDTO,
)
from biolink_backend.application.ports import (
    AdminDocumentPort,
    AdminRelationalPort,
    GraphProjectionPort,
    OutboxRepositoryPort,
)
from biolink_backend.domain.vocabularies import AssertionStatus, DossierSyncStatus, ReviewAction, RoleCode
from biolink_backend.security.rbac import AuthenticatedUser, ensure_has_any_role
from biolink_backend.shared.exceptions import (
    BusinessRuleViolationError,
    DomainValidationError,
    EntityNotFoundError,
)
from biolink_backend.shared.ids import generate_domain_id
from biolink_backend.shared.logging import get_logger
from biolink_backend.workers.outbox_worker import OutboxProjectionWorker

logger = get_logger(__name__)

_REVIEW_ACTION_TO_TARGET_STATUS: dict[ReviewAction, AssertionStatus] = {
    ReviewAction.APPROVE: AssertionStatus.PUBLISHED,
    ReviewAction.REJECT: AssertionStatus.REJECTED,
    ReviewAction.WITHDRAW: AssertionStatus.WITHDRAWN,
    ReviewAction.REOPEN: AssertionStatus.IN_REVIEW,
}


class AdminCurationService:
    """Caso de uso administrativo (OP-09 a OP-14), RBAC diferenciado y reconciliacion multi-modelo."""

    def __init__(
        self,
        *,
        admin_relational: AdminRelationalPort,
        admin_document: AdminDocumentPort,
        outbox_repo: OutboxRepositoryPort,
        graph_projection: GraphProjectionPort,
    ) -> None:
        self._relational = admin_relational
        self._document = admin_document
        self._outbox = outbox_repo
        self._graph = graph_projection

    def register_import_batch(
        self, *, user: AuthenticatedUser, data: ImportBatchCreateInputDTO
    ) -> ImportBatchDetailDTO:
        """OP-09: Solo CURATOR registra lotes, conserva raw_source_record y nunca autopublica."""
        ensure_has_any_role(user, (RoleCode.CURATOR,))
        seen_ids: set[str] = set()
        for rec in data.records:
            if rec.external_record_id in seen_ids:
                raise DomainValidationError(
                    f"Identificador externo duplicado dentro del mismo lote: '{rec.external_record_id}'.",
                    details={"external_record_id": rec.external_record_id},
                )
            seen_ids.add(rec.external_record_id)
        batch_id = generate_domain_id("import_batch")
        effective_license = data.license_id or "ACADEMIC_CITATION_ONLY"
        raw_doc_ids = self._document.insert_raw_source_records(
            import_batch_id=batch_id,
            source_id=data.source_id,
            provider=data.provider,
            license_id=effective_license,
            records=data.records,
        )
        return self._relational.create_import_batch(
            data=data,
            performed_by=user.user_id,
            raw_document_ids=raw_doc_ids,
            batch_id=batch_id,
        )

    def get_import_batch_detail(
        self, *, user: AuthenticatedUser, import_batch_id: str
    ) -> ImportBatchDetailDTO:
        ensure_has_any_role(user, (RoleCode.CURATOR, RoleCode.REVIEWER, RoleCode.ADMIN))
        detail = self._relational.get_import_batch_detail(import_batch_id)
        if detail is None:
            raise EntityNotFoundError("import_batch", import_batch_id)
        return detail

    def list_import_records(
        self,
        *,
        user: AuthenticatedUser,
        import_batch_id: str | None = None,
        validation_status: str | None = None,
        limit: int = 100,
    ) -> list[ImportRecordDTO]:
        ensure_has_any_role(user, (RoleCode.CURATOR, RoleCode.REVIEWER, RoleCode.ADMIN))
        return self._relational.list_import_records(
            import_batch_id=import_batch_id,
            validation_status=validation_status,
            limit=limit,
        )

    def normalize_import_record(
        self, *, user: AuthenticatedUser, data: NormalizeImportRecordInputDTO
    ) -> NormalizedImportRecordResultDTO:
        """OP-10: Solo CURATOR normaliza taxones preservando registro crudo inmutable."""
        ensure_has_any_role(user, (RoleCode.CURATOR,))
        result, raw_doc_id = self._relational.normalize_import_record(
            data=data,
            performed_by=user.user_id,
        )
        raw_updated = False
        if raw_doc_id:
            warnings = (
                [data.validation_notes]
                if data.validation_status in ("UNRESOLVED", "INVALID") and data.validation_notes
                else []
            )
            raw_updated = self._document.update_raw_source_transformation(
                raw_document_id=raw_doc_id,
                target_canonical_taxon_id=data.canonical_taxon_id,
                validation_status=data.validation_status,
                warnings=warnings,
            )
        return replace(result, raw_document_updated=raw_updated)

    def create_assertion(
        self, *, user: AuthenticatedUser, data: CreateAssertionInputDTO
    ) -> AdminAssertionDetailDTO:
        """OP-11: Solo CURATOR crea afirmaciones en DRAFT o IN_REVIEW con evidencia explicita."""
        ensure_has_any_role(user, (RoleCode.CURATOR,))
        return self._relational.create_interaction_assertion(
            data=data,
            performed_by=user.user_id,
        )

    def get_assertion_detail(
        self, *, user: AuthenticatedUser, assertion_id: str
    ) -> AdminAssertionDetailDTO:
        ensure_has_any_role(user, (RoleCode.CURATOR, RoleCode.REVIEWER, RoleCode.ADMIN))
        detail = self._relational.get_assertion_admin_detail(assertion_id)
        if detail is None:
            raise EntityNotFoundError("interaction_assertion", assertion_id)
        return detail

    def submit_assertion_for_review(
        self, *, user: AuthenticatedUser, assertion_id: str, reason: str
    ) -> ReviewAssertionResultDTO:
        """OP-11 / OP-12: CURATOR prepara y envia afirmacion DRAFT hacia IN_REVIEW."""
        ensure_has_any_role(user, (RoleCode.CURATOR,))
        return self._relational.transition_assertion_status(
            assertion_id=assertion_id,
            new_status=AssertionStatus.IN_REVIEW.value,
            reason=reason,
            performed_by=user.user_id,
        )

    def review_assertion(
        self,
        *,
        user: AuthenticatedUser,
        assertion_id: str,
        action: str,
        reason: str,
    ) -> ReviewAssertionResultDTO:
        """OP-12: Solo REVIEWER puede aprobar, rechazar, retirar o reabrir afirmaciones."""
        ensure_has_any_role(user, (RoleCode.REVIEWER,))
        try:
            review_action = ReviewAction(action)
        except ValueError as exc:
            raise DomainValidationError(
                f"Accion de revision invalida: '{action}'.",
                details={"allowed_actions": [a.value for a in _REVIEW_ACTION_TO_TARGET_STATUS]},
            ) from exc

        if review_action not in _REVIEW_ACTION_TO_TARGET_STATUS:
            raise BusinessRuleViolationError(
                "RD-13",
                f"La accion '{review_action.value}' corresponde al flujo de curaduria, no al de revision editorial.",
            )

        target_status = _REVIEW_ACTION_TO_TARGET_STATUS[review_action]
        return self._relational.transition_assertion_status(
            assertion_id=assertion_id,
            new_status=target_status.value,
            reason=reason,
            performed_by=user.user_id,
        )

    def update_taxon_dossier(
        self,
        *,
        user: AuthenticatedUser,
        taxon_id: str,
        data: UpdateTaxonDossierInputDTO,
    ) -> UpdateTaxonDossierResultDTO:
        """OP-13: Solo CURATOR actualiza ficha documental conservando versiones previas y auditoria."""
        ensure_has_any_role(user, (RoleCode.CURATOR,))
        cited_sources = list(data.summary_source_refs) + [c.source_id for c in data.claims]
        if not cited_sources:
            raise BusinessRuleViolationError(
                "RD-02",
                "La actualizacion de ficha documental requiere referencias de fuente en resumen o claims.",
            )
        taxon_summary = self._relational.ensure_taxon_and_sources_exist(taxon_id, cited_sources)

        updated_dossier, prev_version, archived_version_id = self._document.update_taxon_dossier_versioned(
            taxon_id=taxon_id,
            accepted_name=taxon_summary.accepted_name,
            data=data,
            performed_by=user.user_id,
        )

        try:
            self._relational.record_taxon_dossier_version(
                taxon_id=taxon_id,
                dossier_version=updated_dossier.document_version,
                sync_status=DossierSyncStatus.SYNCED.value,
                performed_by=user.user_id,
                details={
                    "previous_version": prev_version,
                    "new_version": updated_dossier.document_version,
                    "archived_version_id": archived_version_id,
                    "change_reason": data.change_reason,
                    "claims_count": len(data.claims),
                },
            )
            return UpdateTaxonDossierResultDTO(
                taxon_id=taxon_id,
                previous_version=prev_version,
                archived_version_id=archived_version_id,
                dossier=updated_dossier,
                dossier_sync_status=DossierSyncStatus.SYNCED.value,
                sync_warning=None,
            )
        except Exception as exc:  # noqa: BLE001
            # Divergencia controlada entre MongoDB (ya actualizado) y PostgreSQL (fallo al auditar)
            logger.error(
                "Divergencia controlada en OP-13 entre MongoDB y PostgreSQL",
                extra={"taxon_id": taxon_id, "new_version": updated_dossier.document_version, "error": str(exc)},
            )
            try:
                self._relational.mark_taxon_dossier_divergence(
                    taxon_id=taxon_id,
                    sync_status=DossierSyncStatus.PENDING_RECONCILIATION.value,
                    error_detail=str(exc),
                    performed_by=user.user_id,
                )
            except Exception:  # noqa: BLE001
                pass
            return UpdateTaxonDossierResultDTO(
                taxon_id=taxon_id,
                previous_version=prev_version,
                archived_version_id=archived_version_id,
                dossier=updated_dossier,
                dossier_sync_status=DossierSyncStatus.PENDING_RECONCILIATION.value,
                sync_warning=(
                    "La nueva version fue escrita en MongoDB, pero fallo el registro relacional de control "
                    f"({exc}). Ejecute reconciliacion administrativa."
                ),
            )

    def get_provenance_history(
        self, *, user: AuthenticatedUser, entity_type: str, entity_id: str
    ) -> ProvenanceHistoryResultDTO:
        """OP-14: CURATOR o REVIEWER consultan procedencia completa de fuentes, lotes, revisiones y outbox."""
        ensure_has_any_role(user, (RoleCode.CURATOR, RoleCode.REVIEWER, RoleCode.ADMIN))
        history = self._relational.get_provenance_history(entity_type=entity_type, entity_id=entity_id)
        if history is None:
            raise EntityNotFoundError(entity_type, entity_id)
        if history.entity_type == "taxon":
            versions = self._document.get_taxon_dossier_version_history(entity_id)
            history = replace(history, dossier_versions=versions)
        return history

    def reconcile_system_state(self, *, user: AuthenticatedUser) -> ReconciliationReportDTO:
        """Reconciliacion administrativa multi-motor: Outbox -> Neo4j y MongoDB <-> PostgreSQL."""
        ensure_has_any_role(user, (RoleCode.REVIEWER, RoleCode.ADMIN))

        # 1. Reencolar eventos FAILED/DEAD_LETTER y drenar Outbox pendiente
        self._outbox.reset_failed_and_dead_letter_events()
        worker = OutboxProjectionWorker(
            outbox_repo=self._outbox,
            graph_repo=self._graph,
        )
        outbox_processed, outbox_failed = worker.drain_once(max_batches=20)

        # 2. Reconciliar estado completo PUBLISHED de PostgreSQL hacia Neo4j
        published_payloads = self._relational.list_all_published_assertion_payloads()
        graph_stats = self._graph.reconcile_published_assertions(published_payloads)
        published_ids = [str(p["assertion_id"]) for p in published_payloads]
        if published_ids:
            self._relational.mark_assertions_projection_synced(published_ids)

        # 3. Reconciliar versiones taxon_dossier de MongoDB vs PostgreSQL
        pg_taxa = self._relational.list_taxa_with_dossier_state()
        mongo_versions = self._document.list_all_taxon_dossier_versions()
        reconciled_taxa: list[str] = []
        for taxon_id, pg_ver, sync_status in pg_taxa:
            mongo_ver = mongo_versions.get(taxon_id, 0)
            if mongo_ver != pg_ver or sync_status != DossierSyncStatus.SYNCED.value:
                self._relational.record_taxon_dossier_version(
                    taxon_id=taxon_id,
                    dossier_version=mongo_ver,
                    sync_status=DossierSyncStatus.SYNCED.value,
                    performed_by=user.user_id,
                    details={
                        "reconciled": True,
                        "previous_pg_version": pg_ver,
                        "mongo_version": mongo_ver,
                        "previous_sync_status": sync_status,
                    },
                )
                reconciled_taxa.append(taxon_id)

        return ReconciliationReportDTO(
            outbox_processed_count=outbox_processed,
            outbox_failed_count=outbox_failed,
            graph_expected_published_count=len(published_payloads),
            graph_projected_or_verified_count=graph_stats["projected_or_verified"],
            graph_removed_stale_count=graph_stats["removed_stale"],
            dossier_checked_taxa_count=len(pg_taxa),
            dossier_reconciled_count=len(reconciled_taxa),
            reconciled_taxon_ids=reconciled_taxa,
        )
