from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from biolink_backend.application.dto import (
    AdminAssertionDetailDTO,
    AssertionEvidenceInputDTO,
    AuditLogEntryDTO,
    CreateAssertionInputDTO,
    EvidenceDetailDTO,
    GraphAssertionEdgeDTO,
    ImportBatchCreateInputDTO,
    ImportBatchDetailDTO,
    ImportBatchDTO,
    ImportRecordDTO,
    InteractionListItemDTO,
    LocalitySummaryDTO,
    NormalizeImportRecordInputDTO,
    NormalizedImportRecordResultDTO,
    OccurrenceItemDTO,
    OutboxEventDTO,
    PaginatedResult,
    ProvenanceHistoryResultDTO,
    RawSourceReferenceDTO,
    ReviewAssertionResultDTO,
    ReviewEventDTO,
    SourceSummaryDTO,
    SourceVersionDTO,
    StudyDossierDTO,
    TaxonDossierDTO,
    TaxonDossierVersionDTO,
    TaxonIdentifierDTO,
    TaxonNameDTO,
    TaxonSummaryDTO,
    UpdateTaxonDossierInputDTO,
)
from biolink_backend.domain.entities import OutboxEvent
from biolink_backend.domain.vocabularies import RoleCode


@dataclass(frozen=True, slots=True)
class EngineHealthStatus:
    """Resultado estructurado de verificacion de salud de un motor."""

    engine: str
    healthy: bool
    latency_ms: float
    version: str | None = None
    error: str | None = None


class RelationalHealthPort(Protocol):
    def check_health(self) -> EngineHealthStatus: ...


class DocumentHealthPort(Protocol):
    def check_health(self) -> EngineHealthStatus: ...


class GraphHealthPort(Protocol):
    def check_health(self) -> EngineHealthStatus: ...


@dataclass(frozen=True, slots=True)
class StoredUserRecord:
    user_id: str
    display_name: str
    email: str
    password_hash: str
    is_active: bool
    roles: frozenset[RoleCode]


@dataclass(frozen=True, slots=True)
class StoredSessionRecord:
    session_id: str
    user_id: str
    email: str
    display_name: str
    is_active: bool
    roles: frozenset[RoleCode]
    expires_at: datetime


class AuthRepositoryPort(Protocol):
    def get_user_by_email(self, email: str) -> StoredUserRecord | None: ...

    def create_session(
        self,
        *,
        session_id: str,
        user_id: str,
        token_hash: str,
        expires_at: datetime,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> None: ...

    def get_active_session_by_token_hash(self, token_hash: str) -> StoredSessionRecord | None: ...

    def revoke_session_by_token_hash(self, token_hash: str) -> bool: ...


class OutboxRepositoryPort(Protocol):
    def claim_pending_events(self, limit: int, *, max_attempts: int = 5) -> list[OutboxEvent]: ...

    def mark_processed(self, outbox_event_id: str) -> None: ...

    def mark_failed(
        self,
        outbox_event_id: str,
        *,
        error_message: str,
        max_attempts: int,
    ) -> None: ...

    def reset_failed_and_dead_letter_events(self, assertion_id: str | None = None) -> int: ...

    def list_events_for_aggregate(self, aggregate_id: str) -> list[OutboxEventDTO]: ...


class GraphProjectionPort(Protocol):
    def project_assertion(self, payload: dict[str, Any]) -> str: ...

    def withdraw_assertion(self, assertion_id: str) -> int: ...

    def withdraw_assertions_batch(self, assertion_ids: list[str]) -> int: ...

    def list_projected_assertion_ids(self) -> set[str]: ...

    def reconcile_published_assertions(
        self, expected_payloads: list[dict[str, Any]]
    ) -> dict[str, int]: ...


class CatalogRelationalPort(Protocol):
    def search_taxa(
        self,
        *,
        query: str | None,
        taxonomic_rank: str | None,
        organism_group: str | None,
        taxonomic_status: str | None,
        sort_by: str,
        sort_order: str,
        page: int,
        page_size: int,
    ) -> PaginatedResult[TaxonSummaryDTO]: ...

    def get_taxon_by_id(self, taxon_id: str) -> TaxonSummaryDTO | None: ...

    def get_taxa_by_ids(self, taxon_ids: list[str]) -> dict[str, TaxonSummaryDTO]: ...

    def get_taxon_names(self, taxon_id: str) -> list[TaxonNameDTO]: ...

    def get_taxon_identifiers(self, taxon_id: str) -> list[TaxonIdentifierDTO]: ...

    def get_locality_by_id(self, locality_id: str) -> LocalitySummaryDTO | None: ...

    def get_localities_by_ids(self, locality_ids: list[str]) -> dict[str, LocalitySummaryDTO]: ...

    def get_published_assertion_by_id(self, assertion_id: str) -> InteractionListItemDTO | None: ...

    def get_published_assertions_by_ids(
        self, assertion_ids: list[str]
    ) -> dict[str, InteractionListItemDTO]: ...

    def get_evidences_for_assertion(self, assertion_id: str) -> list[EvidenceDetailDTO]: ...

    def count_undetermined_published_assertions(
        self,
        *,
        period_start: str | None = None,
        period_end: str | None = None,
        interaction_type_code: str | None = None,
        method_code: str | None = None,
        source_id: str | None = None,
    ) -> int: ...

    def get_source_by_id(self, source_id: str) -> SourceSummaryDTO | None: ...

    def get_source_versions(self, source_id: str) -> list[SourceVersionDTO]: ...

    def get_source_usage_counts(self, source_id: str) -> tuple[int, int]: ...

    def list_occurrences(
        self,
        *,
        taxon_id: str | None,
        locality_id: str | None,
        source_id: str | None,
        georeference_status: str | None,
        date_from: str | None,
        date_to: str | None,
        sort_by: str,
        sort_order: str,
        page: int,
        page_size: int,
    ) -> PaginatedResult[OccurrenceItemDTO]: ...


class DossierDocumentPort(Protocol):
    def get_taxon_dossier(self, taxon_id: str) -> TaxonDossierDTO | None: ...

    def get_study_dossier(self, source_id: str) -> StudyDossierDTO | None: ...

    def get_study_dossiers_by_source_ids(self, source_ids: list[str]) -> list[StudyDossierDTO]: ...

    def get_raw_source_record_by_external_id(
        self, source_id: str, external_record_id: str
    ) -> RawSourceReferenceDTO | None: ...


class NetworkGraphPort(Protocol):
    def list_taxon_interactions(
        self,
        *,
        taxon_id: str,
        role: str,
        interaction_type_code: str | None,
        locality_id: str | None,
        method_code: str | None,
        period_start: str | None,
        period_end: str | None,
        page: int,
        page_size: int,
    ) -> tuple[list[GraphAssertionEdgeDTO], int]: ...

    def is_assertion_projected(self, assertion_id: str) -> bool: ...

    def get_filtered_subgraph(
        self,
        *,
        taxon_id: str | None,
        locality_id: str | None,
        protected_status: str | None,
        spatial_validation_status: str | None,
        interaction_type_code: str | None,
        method_code: str | None,
        source_id: str | None,
        period_start: str | None,
        period_end: str | None,
        include_undetermined_locality: bool,
        max_depth: int,
        limit: int,
    ) -> list[GraphAssertionEdgeDTO]: ...

    def get_locality_assertions(
        self,
        *,
        locality_id: str,
        interaction_type_code: str | None,
        period_start: str | None,
        period_end: str | None,
    ) -> list[GraphAssertionEdgeDTO]: ...

    def get_taxon_connectivity_edges(
        self,
        *,
        taxon_id: str,
        locality_id: str | None,
        interaction_type_code: str | None,
        period_start: str | None,
        period_end: str | None,
    ) -> list[GraphAssertionEdgeDTO]: ...


class AdminRelationalPort(Protocol):
    def create_import_batch(
        self,
        *,
        data: ImportBatchCreateInputDTO,
        performed_by: str,
        raw_document_ids: dict[str, str],
        batch_id: str | None = None,
    ) -> ImportBatchDetailDTO: ...

    def get_import_batch_detail(self, import_batch_id: str) -> ImportBatchDetailDTO | None: ...

    def list_import_records(
        self,
        *,
        import_batch_id: str | None = None,
        validation_status: str | None = None,
        limit: int = 100,
    ) -> list[ImportRecordDTO]: ...

    def normalize_import_record(
        self,
        *,
        data: NormalizeImportRecordInputDTO,
        performed_by: str,
    ) -> tuple[NormalizedImportRecordResultDTO, str | None]: ...

    def create_interaction_assertion(
        self,
        *,
        data: CreateAssertionInputDTO,
        performed_by: str,
    ) -> AdminAssertionDetailDTO: ...

    def get_assertion_admin_detail(self, assertion_id: str) -> AdminAssertionDetailDTO | None: ...

    def transition_assertion_status(
        self,
        *,
        assertion_id: str,
        new_status: str,
        reason: str,
        performed_by: str,
    ) -> ReviewAssertionResultDTO: ...

    def ensure_taxon_and_sources_exist(
        self, taxon_id: str, source_ids: list[str]
    ) -> TaxonSummaryDTO: ...

    def record_taxon_dossier_version(
        self,
        *,
        taxon_id: str,
        dossier_version: int,
        sync_status: str,
        performed_by: str,
        details: dict[str, Any],
    ) -> None: ...

    def mark_taxon_dossier_divergence(
        self,
        *,
        taxon_id: str,
        sync_status: str,
        error_detail: str,
        performed_by: str,
    ) -> None: ...

    def list_taxa_with_dossier_state(self) -> list[tuple[str, int, str]]: ...

    def list_all_published_assertion_payloads(self) -> list[dict[str, Any]]: ...

    def mark_assertions_projection_synced(self, assertion_ids: list[str]) -> int: ...

    def get_provenance_history(
        self, *, entity_type: str, entity_id: str
    ) -> ProvenanceHistoryResultDTO | None: ...


class AdminDocumentPort(Protocol):
    def insert_raw_source_records(
        self,
        *,
        import_batch_id: str,
        source_id: str,
        provider: str,
        license_id: str,
        records: list[ImportRecordInputDTO],
    ) -> dict[str, str]: ...

    def update_raw_source_transformation(
        self,
        *,
        raw_document_id: str,
        target_canonical_taxon_id: str | None,
        validation_status: str,
        warnings: list[str] | None = None,
    ) -> bool: ...

    def update_taxon_dossier_versioned(
        self,
        *,
        taxon_id: str,
        accepted_name: str,
        data: UpdateTaxonDossierInputDTO,
        performed_by: str,
    ) -> tuple[TaxonDossierDTO, int | None, str | None]: ...

    def get_taxon_dossier_version_history(
        self, taxon_id: str
    ) -> list[TaxonDossierVersionDTO]: ...

    def list_all_taxon_dossier_versions(self) -> dict[str, int]: ...
