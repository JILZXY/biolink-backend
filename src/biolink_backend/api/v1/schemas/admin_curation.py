from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from biolink_backend.domain.vocabularies import (
    EvidenceKind,
    EvidenceMethodCode,
    ImportRecordValidationStatus,
    InteractionTypeCode,
    TaxonIdentifierScheme,
)

# ----------------------------------------------------------------------------
# Autenticacion y Sesiones
# ----------------------------------------------------------------------------


class LoginRequestSchema(BaseModel):
    email: str = Field(min_length=4, max_length=255)
    password: str = Field(min_length=8, max_length=256)


class AuthenticatedUserSchema(BaseModel):
    user_id: str
    email: str
    display_name: str
    roles: list[str]
    is_active: bool


class AuthSessionResponseSchema(BaseModel):
    token_type: Literal["Bearer"] = "Bearer"
    access_token: str
    expires_at: datetime
    user: AuthenticatedUserSchema


class LogoutResponseSchema(BaseModel):
    revoked: bool
    message: str


# ----------------------------------------------------------------------------
# OP-09: Registro de lotes de importacion
# ----------------------------------------------------------------------------


class ImportRecordCreateSchema(BaseModel):
    external_record_id: str = Field(min_length=1, max_length=128)
    raw_payload: dict[str, Any] = Field(min_length=1)
    verbatim_name: str | None = Field(default=None, max_length=255)
    proposed_external_scheme: TaxonIdentifierScheme | None = None
    proposed_external_id: str | None = Field(default=None, max_length=128)
    redistribution_permitted: bool = False
    full_text_stored: bool = False


class ImportBatchCreateRequestSchema(BaseModel):
    source_id: str = Field(min_length=1, max_length=64)
    provider_version: str = Field(min_length=1, max_length=64)
    endpoint_or_file: str = Field(min_length=1, max_length=512)
    provider: str = Field(min_length=1, max_length=64)
    query_parameters: dict[str, Any] = Field(default_factory=dict)
    filters: dict[str, Any] = Field(default_factory=dict)
    records: list[ImportRecordCreateSchema] = Field(min_length=1, max_length=500)
    license_id: str | None = Field(default=None, max_length=64)
    release_date: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    auto_publish: bool = False


class ImportRecordResponseSchema(BaseModel):
    import_record_id: str
    import_batch_id: str
    external_record_id: str
    raw_document_id: str | None = None
    validation_status: str
    canonical_taxon_id: str | None = None
    canonical_accepted_name: str | None = None
    verbatim_name: str | None = None
    proposed_external_scheme: str | None = None
    proposed_external_id: str | None = None
    matching_rule: str | None = None
    validation_notes: str | None = None
    normalized_by: str | None = None
    normalized_at: str | None = None
    created_at: str


class ImportBatchResponseSchema(BaseModel):
    import_batch_id: str
    source_id: str
    source_version_id: str | None = None
    provider_version: str | None = None
    endpoint_or_file: str | None = None
    started_by: str
    started_at: str
    finished_at: str | None = None
    query_parameters: dict[str, Any]
    filters: dict[str, Any]
    status: str
    received_count: int
    accepted_count: int
    rejected_count: int
    pending_count: int
    unresolved_count: int


class ImportBatchDetailResponseSchema(BaseModel):
    batch: ImportBatchResponseSchema
    records: list[ImportRecordResponseSchema]


# ----------------------------------------------------------------------------
# OP-10: Validacion y normalizacion de registros
# ----------------------------------------------------------------------------


class NormalizeImportRecordRequestSchema(BaseModel):
    validation_status: Literal["VALID", "INVALID", "UNRESOLVED", "DUPLICATE"]
    matching_rule: str = Field(min_length=2, max_length=128)
    canonical_taxon_id: str | None = Field(default=None, max_length=64)
    verbatim_name: str | None = Field(default=None, max_length=255)
    external_scheme: TaxonIdentifierScheme | None = None
    external_id: str | None = Field(default=None, max_length=128)
    validation_notes: str | None = Field(default=None, max_length=2000)
    register_name_variant: bool = True
    register_external_identifier: bool = True


class NormalizeImportRecordResponseSchema(BaseModel):
    record: ImportRecordResponseSchema
    batch: ImportBatchResponseSchema
    registered_name_id: str | None = None
    registered_identifier_id: str | None = None
    raw_document_updated: bool


# ----------------------------------------------------------------------------
# OP-11 & OP-12: Afirmaciones, revision y Outbox
# ----------------------------------------------------------------------------


class AssertionEvidenceInputSchema(BaseModel):
    evidence_role: Literal["PRIMARY", "SUPPORTING", "CONTEXTUAL"] = "PRIMARY"
    review_note: str | None = Field(default=None, max_length=512)
    evidence_id: str | None = Field(default=None, max_length=64)
    source_id: str | None = Field(default=None, max_length=64)
    source_version_id: str | None = Field(default=None, max_length=64)
    source_locator: str | None = Field(default=None, max_length=255)
    evidence_kind: EvidenceKind | None = None
    method_code: EvidenceMethodCode | None = None
    reported_term: str | None = Field(default=None, max_length=128)
    summary: str | None = None
    interpretation_limit: str | None = None
    period_start: str | None = Field(default=None, max_length=32)
    period_end: str | None = Field(default=None, max_length=32)
    license_id: str | None = Field(default=None, max_length=64)


class CreateAssertionRequestSchema(BaseModel):
    actor_taxon_id: str = Field(min_length=1, max_length=64)
    target_taxon_id: str = Field(min_length=1, max_length=64)
    interaction_type_code: InteractionTypeCode
    locality_id: str = Field(min_length=1, max_length=64)
    period_start: str = Field(min_length=4, max_length=32)
    period_end: str = Field(min_length=4, max_length=32)
    season_label: str = Field(min_length=1, max_length=128)
    method_code: EvidenceMethodCode
    assertion_scope: str = Field(default="BIOLINK_V1", min_length=1, max_length=64)
    initial_status: Literal["DRAFT", "IN_REVIEW"] = "DRAFT"
    evidences: list[AssertionEvidenceInputSchema] = Field(min_length=1)
    submission_reason: str | None = Field(default=None, max_length=500)


class SubmitAssertionRequestSchema(BaseModel):
    reason: str = Field(min_length=3, max_length=1000)


class ReviewAssertionRequestSchema(BaseModel):
    action: Literal["APPROVE", "REJECT", "WITHDRAW", "REOPEN"]
    reason: str = Field(min_length=3, max_length=2000)


class OutboxEventSchema(BaseModel):
    event_id: str
    event_type: str
    assertion_id: str
    aggregate_version: int
    payload_version: int
    created_at: str
    processing_status: str
    attempts: int
    last_error: str | None = None
    processed_at: str | None = None
    payload_json: dict[str, Any] = Field(default_factory=dict)


class ReviewEventSchema(BaseModel):
    review_event_id: str
    assertion_id: str
    user_id: str
    previous_status: str
    new_status: str
    reason: str
    created_at: str


class AdminAssertionResponseSchema(BaseModel):
    assertion_id: str
    status: str
    aggregate_version: int
    projection_status: str
    last_projected_at: str | None = None
    interaction_type_code: str
    actor_taxon: dict[str, Any]
    target_taxon: dict[str, Any]
    locality: dict[str, Any]
    period_start: str
    period_end: str
    season_label: str
    method_code: str
    assertion_scope: str
    created_by: str
    created_at: str
    reviewed_by: str | None = None
    reviewed_at: str | None = None
    updated_at: str
    evidences: list[dict[str, Any]]
    graph_projected: bool


class ReviewAssertionResponseSchema(BaseModel):
    assertion: AdminAssertionResponseSchema
    review_event: ReviewEventSchema
    outbox_event: OutboxEventSchema | None = None
    relational_committed: bool
    graph_sync_note: str


# ----------------------------------------------------------------------------
# OP-13: Actualizacion versionada de expediente documental de taxon
# ----------------------------------------------------------------------------


class DossierClaimInputSchema(BaseModel):
    claim_id: str = Field(min_length=1, max_length=64)
    field: str = Field(min_length=1, max_length=128)
    value: Any
    source_id: str = Field(min_length=1, max_length=64)
    source_locator: str = Field(min_length=1, max_length=255)


class DossierMediaInputSchema(BaseModel):
    media_id: str = Field(min_length=1, max_length=64)
    media_type: Literal["image", "audio", "video", "document"]
    uri: str = Field(min_length=1, max_length=512)
    license: str = Field(min_length=1, max_length=128)
    attribution: str = Field(min_length=1, max_length=255)
    source_ref: str = Field(min_length=1, max_length=128)
    checksum: str | None = None


class UpdateTaxonDossierRequestSchema(BaseModel):
    summary_description: str = Field(min_length=3, max_length=4000)
    summary_source_refs: list[str] = Field(min_length=1)
    summary_locators: list[str] = Field(default_factory=list)
    sections: dict[str, Any] = Field(default_factory=dict)
    claims: list[DossierClaimInputSchema] = Field(default_factory=list)
    media: list[DossierMediaInputSchema] = Field(default_factory=list)
    change_reason: str = Field(min_length=3, max_length=1000)
    expected_version: int | None = Field(default=None, ge=0)
    dataset_tag: str = Field(default="CURATED_BIOLINK", min_length=1, max_length=64)


class TaxonDossierVersionSchema(BaseModel):
    version_id: str
    taxon_id: str
    document_version: int
    archived_at: str
    archived_by: str | None = None
    change_reason: str | None = None
    snapshot: dict[str, Any] | None = None


class UpdateTaxonDossierResponseSchema(BaseModel):
    taxon_id: str
    previous_version: int | None = None
    archived_version_id: str | None = None
    dossier: dict[str, Any]
    dossier_sync_status: str
    sync_warning: str | None = None


# ----------------------------------------------------------------------------
# OP-14 y Reconciliacion Multi-Modelo
# ----------------------------------------------------------------------------


class AuditLogEntrySchema(BaseModel):
    audit_id: int
    entity_type: str
    entity_id: str
    action: str
    performed_by: str | None = None
    performed_at: str
    details: dict[str, Any]


class ProvenanceHistoryResponseSchema(BaseModel):
    entity_type: str
    entity_id: str
    current_state: dict[str, Any]
    sources: list[dict[str, Any]]
    source_versions: list[dict[str, Any]]
    import_batches: list[ImportBatchResponseSchema]
    import_records: list[ImportRecordResponseSchema]
    evidences: list[dict[str, Any]]
    review_events: list[ReviewEventSchema]
    outbox_events: list[OutboxEventSchema]
    dossier_versions: list[TaxonDossierVersionSchema]
    audit_logs: list[AuditLogEntrySchema]


class ReconciliationResponseSchema(BaseModel):
    outbox_processed_count: int
    outbox_failed_count: int
    graph_expected_published_count: int
    graph_projected_or_verified_count: int
    graph_removed_stale_count: int
    dossier_checked_taxa_count: int
    dossier_reconciled_count: int
    reconciled_taxon_ids: list[str]


ImportRecordStatusFilter = ImportRecordValidationStatus
