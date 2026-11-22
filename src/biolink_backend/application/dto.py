from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar

T = TypeVar("T")

DISCLAIMER_OCCURRENCE_NOT_INTERACTION = (
    "Los registros de ocurrencia documentan exclusivamente presencia o deteccion del taxon; "
    "no constituyen evidencia ni implican interaccion ecologica (RD-01, RD-06)."
)
DISCLAIMER_FRUGIVORY_NO_DISPERSAL = (
    "El consumo, paso por tracto digestivo o remocion de frutos se reporta segun la resolucion "
    "de la fuente original; por si solo no demuestra germinacion ni dispersion efectiva (RD-04, RD-05)."
)
DISCLAIMER_CONNECTIVITY_NOT_ABUNDANCE = (
    "El grado y los conteos de conectividad representan unicamente taxones asociados distintos "
    "en la red documentada bajo los filtros aplicados; no representan abundancia poblacional ni intensidad ecologica (RD-12)."
)
DISCLAIMER_LOCALITY_SEPARATION = (
    "Montes Azules (INSIDE_ANP) y Ejido Playon de la Gloria (OUTSIDE_ANP) se mantienen como contextos "
    "geograficos separados; los registros indeterminados no se asignan al ANP (RD-07, RD-08)."
)


@dataclass(frozen=True, slots=True)
class PaginatedResult(Generic[T]):
    items: list[T]
    total: int
    page: int
    page_size: int


@dataclass(frozen=True, slots=True)
class TaxonNameDTO:
    taxon_name_id: str
    name: str
    name_usage: str
    language: str
    source_id: str
    is_preferred: bool
    verbatim_name: str | None = None


@dataclass(frozen=True, slots=True)
class TaxonIdentifierDTO:
    taxon_identifier_id: str
    scheme: str
    external_id: str
    source_id: str


@dataclass(frozen=True, slots=True)
class TaxonSummaryDTO:
    taxon_id: str
    accepted_name: str
    scientific_authorship: str
    taxonomic_rank: str
    organism_group: str
    taxonomic_status: str
    matched_name: str | None = None
    matched_usage: str | None = None
    matched_verbatim_name: str | None = None
    matched_identifier: str | None = None


@dataclass(frozen=True, slots=True)
class TaxonDossierClaimDTO:
    claim_id: str
    field: str
    value: Any
    source_id: str
    source_locator: str


@dataclass(frozen=True, slots=True)
class TaxonDossierMediaDTO:
    media_id: str
    media_type: str
    uri: str
    license: str
    attribution: str
    source_ref: str
    checksum: str | None = None


@dataclass(frozen=True, slots=True)
class TaxonDossierDTO:
    taxon_id: str
    schema_version: int
    document_version: int
    history_versions_count: int
    summary_description: str | None
    summary_source_refs: list[str]
    summary_locators: list[str]
    sections: dict[str, Any]
    claims: list[TaxonDossierClaimDTO]
    media: list[TaxonDossierMediaDTO]
    updated_at: str


@dataclass(frozen=True, slots=True)
class TaxonDetailResultDTO:
    taxon: TaxonSummaryDTO
    names: list[TaxonNameDTO]
    external_identifiers: list[TaxonIdentifierDTO]
    has_dossier: bool
    dossier: TaxonDossierDTO | None
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class LocalitySummaryDTO:
    locality_id: str
    name: str
    locality_type: str
    protected_status: str
    spatial_validation_status: str
    coordinate_precision: str | None
    parent_locality_id: str | None


@dataclass(frozen=True, slots=True)
class SourceSummaryDTO:
    source_id: str
    source_type: str
    title: str
    authors: str
    publication_year: int
    citation_text: str
    license_id: str
    doi: str | None = None
    uri: str | None = None
    publisher: str | None = None


@dataclass(frozen=True, slots=True)
class EvidenceDetailDTO:
    evidence_id: str
    source_id: str
    source_version_id: str | None
    source_locator: str
    evidence_kind: str
    method_code: str
    reported_term: str
    summary: str
    interpretation_limit: str
    period_start: str
    period_end: str
    license_id: str
    evidence_role: str
    review_note: str | None
    source: SourceSummaryDTO


@dataclass(frozen=True, slots=True)
class InteractionListItemDTO:
    assertion_id: str
    status: str
    interaction_type_code: str
    actor_taxon: TaxonSummaryDTO
    target_taxon: TaxonSummaryDTO
    locality: LocalitySummaryDTO
    period_start: str
    period_end: str
    season_label: str
    method_code: str
    assertion_scope: str
    source_citations: list[str]
    source_locators: list[str]
    interpretation_limits: list[str]


@dataclass(frozen=True, slots=True)
class GraphAssertionEdgeDTO:
    assertion_id: str
    actor_taxon_id: str
    target_taxon_id: str
    interaction_type_code: str
    locality_id: str
    period_start: str
    period_end: str
    season_label: str
    method_code: str
    source_ids: list[str]


@dataclass(frozen=True, slots=True)
class RawSourceReferenceDTO:
    raw_document_id: str
    import_batch_id: str
    source_id: str
    external_record_id: str
    provider: str
    checksum: str
    license_id: str
    redistribution_permitted: bool
    raw_payload: dict[str, Any] | None


@dataclass(frozen=True, slots=True)
class InteractionDetailResultDTO:
    assertion: InteractionListItemDTO
    evidences: list[EvidenceDetailDTO]
    study_dossiers: list[StudyDossierDTO]
    raw_source_references: list[RawSourceReferenceDTO]
    graph_projected: bool
    warnings: list[str]


@dataclass(frozen=True, slots=True)
class SubgraphNodeDTO:
    node_id: str
    node_type: str
    label: str
    properties: dict[str, Any]


@dataclass(frozen=True, slots=True)
class SubgraphEdgeDTO:
    assertion_id: str
    actor_taxon_id: str
    actor_name: str
    target_taxon_id: str
    target_name: str
    interaction_type_code: str
    locality_id: str
    locality_name: str
    protected_status: str
    period_start: str
    period_end: str
    method_code: str
    source_ids: list[str]


@dataclass(frozen=True, slots=True)
class SubgraphResultDTO:
    applied_filters: dict[str, Any]
    nodes: list[SubgraphNodeDTO]
    interactions: list[SubgraphEdgeDTO]
    undetermined_locality_excluded_count: int
    warnings: list[str]


@dataclass(frozen=True, slots=True)
class LocalityNetworkMetricsDTO:
    locality: LocalitySummaryDTO
    published_assertions_count: int
    distinct_taxa_count: int
    distinct_animal_taxa_count: int
    distinct_plant_taxa_count: int
    source_ids: list[str]


@dataclass(frozen=True, slots=True)
class SharedInteractionPairDTO:
    actor_taxon_id: str
    actor_name: str
    target_taxon_id: str
    target_name: str
    interaction_type_code: str


@dataclass(frozen=True, slots=True)
class LocalityComparisonResultDTO:
    locality_a: LocalityNetworkMetricsDTO
    locality_b: LocalityNetworkMetricsDTO
    shared_taxa: list[TaxonSummaryDTO]
    exclusive_to_a_taxa: list[TaxonSummaryDTO]
    exclusive_to_b_taxa: list[TaxonSummaryDTO]
    shared_interaction_pairs: list[SharedInteractionPairDTO]
    undetermined_locality_assertions_count: int
    applied_filters: dict[str, Any]
    warnings: list[str]


@dataclass(frozen=True, slots=True)
class PartnerConnectivityDTO:
    taxon: TaxonSummaryDTO
    role_of_partner: str
    assertions_count: int
    interaction_type_codes: list[str]
    locality_ids: list[str]


@dataclass(frozen=True, slots=True)
class ConnectivityResultDTO:
    taxon: TaxonSummaryDTO
    distinct_partner_taxa_count: int
    documented_assertions_count: int
    as_actor_distinct_partners: int
    as_target_distinct_partners: int
    partners: list[PartnerConnectivityDTO]
    applied_filters: dict[str, Any]
    warnings: list[str]


@dataclass(frozen=True, slots=True)
class SourceVersionDTO:
    source_version_id: str
    provider_version: str
    release_date: str | None
    retrieved_at: str
    endpoint_or_file: str
    query_parameters: dict[str, Any]
    filters: dict[str, Any]
    license_id: str
    checksum: str


@dataclass(frozen=True, slots=True)
class StudyDossierDTO:
    dossier_id: str
    source_id: str
    schema_version: int
    doi_or_uri: str | None
    research_question: str
    sampling_design: dict[str, Any]
    study_localities: list[str]
    study_period: dict[str, Any]
    focal_taxa: list[str]
    interpretation_limits: list[str]
    license_and_usage: dict[str, Any]
    checksum: str | None


@dataclass(frozen=True, slots=True)
class SourceDetailResultDTO:
    source: SourceSummaryDTO
    versions: list[SourceVersionDTO]
    published_assertions_count: int
    evidences_count: int
    has_study_dossier: bool
    study_dossier: StudyDossierDTO | None
    warnings: list[str]


@dataclass(frozen=True, slots=True)
class OccurrenceItemDTO:
    occurrence_id: str
    taxon: TaxonSummaryDTO
    source: SourceSummaryDTO
    locality: LocalitySummaryDTO | None
    source_record_id: str | None
    event_date: str | None
    latitude: float | None
    longitude: float | None
    coordinate_uncertainty_meters: float | None
    basis_of_record: str
    georeference_status: str
    is_interaction_evidence: bool
    raw_source_reference: RawSourceReferenceDTO | None = None


# ----------------------------------------------------------------------------
# DTOs Administrativos, Curaduria y Sincronizacion (OP-09 a OP-14)
# ----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ImportRecordInputDTO:
    external_record_id: str
    raw_payload: dict[str, Any]
    verbatim_name: str | None = None
    proposed_external_scheme: str | None = None
    proposed_external_id: str | None = None
    redistribution_permitted: bool = False
    full_text_stored: bool = False


@dataclass(frozen=True, slots=True)
class ImportBatchCreateInputDTO:
    source_id: str
    provider_version: str
    endpoint_or_file: str
    provider: str
    query_parameters: dict[str, Any]
    filters: dict[str, Any]
    records: list[ImportRecordInputDTO]
    license_id: str | None = None
    release_date: str | None = None
    auto_publish: bool = False


@dataclass(frozen=True, slots=True)
class ImportRecordDTO:
    import_record_id: str
    import_batch_id: str
    external_record_id: str
    raw_document_id: str | None
    validation_status: str
    canonical_taxon_id: str | None
    canonical_accepted_name: str | None
    verbatim_name: str | None
    proposed_external_scheme: str | None
    proposed_external_id: str | None
    matching_rule: str | None
    validation_notes: str | None
    normalized_by: str | None
    normalized_at: str | None
    created_at: str


@dataclass(frozen=True, slots=True)
class ImportBatchDTO:
    import_batch_id: str
    source_id: str
    source_version_id: str | None
    provider_version: str | None
    endpoint_or_file: str | None
    started_by: str
    started_at: str
    finished_at: str | None
    query_parameters: dict[str, Any]
    filters: dict[str, Any]
    status: str
    received_count: int
    accepted_count: int
    rejected_count: int
    pending_count: int
    unresolved_count: int


@dataclass(frozen=True, slots=True)
class ImportBatchDetailDTO:
    batch: ImportBatchDTO
    records: list[ImportRecordDTO]


@dataclass(frozen=True, slots=True)
class NormalizeImportRecordInputDTO:
    import_record_id: str
    validation_status: str
    matching_rule: str
    canonical_taxon_id: str | None = None
    verbatim_name: str | None = None
    external_scheme: str | None = None
    external_id: str | None = None
    validation_notes: str | None = None
    register_name_variant: bool = True
    register_external_identifier: bool = True


@dataclass(frozen=True, slots=True)
class NormalizedImportRecordResultDTO:
    record: ImportRecordDTO
    batch: ImportBatchDTO
    registered_name_id: str | None = None
    registered_identifier_id: str | None = None
    raw_document_updated: bool = False


@dataclass(frozen=True, slots=True)
class AssertionEvidenceInputDTO:
    evidence_role: str = "PRIMARY"
    review_note: str | None = None
    evidence_id: str | None = None
    source_id: str | None = None
    source_version_id: str | None = None
    source_locator: str | None = None
    evidence_kind: str | None = None
    method_code: str | None = None
    reported_term: str | None = None
    summary: str | None = None
    interpretation_limit: str | None = None
    period_start: str | None = None
    period_end: str | None = None
    license_id: str | None = None


@dataclass(frozen=True, slots=True)
class CreateAssertionInputDTO:
    actor_taxon_id: str
    target_taxon_id: str
    interaction_type_code: str
    locality_id: str
    period_start: str
    period_end: str
    season_label: str
    method_code: str
    assertion_scope: str = "BIOLINK_V1"
    initial_status: str = "DRAFT"
    evidences: list[AssertionEvidenceInputDTO] = field(default_factory=list)
    submission_reason: str | None = None


@dataclass(frozen=True, slots=True)
class OutboxEventDTO:
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
    payload_json: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ReviewEventDTO:
    review_event_id: str
    assertion_id: str
    user_id: str
    previous_status: str
    new_status: str
    reason: str
    created_at: str


@dataclass(frozen=True, slots=True)
class AdminAssertionDetailDTO:
    assertion_id: str
    status: str
    aggregate_version: int
    projection_status: str
    last_projected_at: str | None
    interaction_type_code: str
    actor_taxon: TaxonSummaryDTO
    target_taxon: TaxonSummaryDTO
    locality: LocalitySummaryDTO
    period_start: str
    period_end: str
    season_label: str
    method_code: str
    assertion_scope: str
    created_by: str
    created_at: str
    reviewed_by: str | None
    reviewed_at: str | None
    updated_at: str
    evidences: list[EvidenceDetailDTO]
    graph_projected: bool = False


@dataclass(frozen=True, slots=True)
class ReviewAssertionResultDTO:
    assertion: AdminAssertionDetailDTO
    review_event: ReviewEventDTO
    outbox_event: OutboxEventDTO | None
    relational_committed: bool
    graph_sync_note: str


@dataclass(frozen=True, slots=True)
class DossierClaimInputDTO:
    claim_id: str
    field: str
    value: Any
    source_id: str
    source_locator: str


@dataclass(frozen=True, slots=True)
class DossierMediaInputDTO:
    media_id: str
    media_type: str
    uri: str
    license: str
    attribution: str
    source_ref: str
    checksum: str | None = None


@dataclass(frozen=True, slots=True)
class UpdateTaxonDossierInputDTO:
    summary_description: str
    summary_source_refs: list[str]
    summary_locators: list[str]
    sections: dict[str, Any]
    claims: list[DossierClaimInputDTO]
    media: list[DossierMediaInputDTO] = field(default_factory=list)
    change_reason: str = "Actualizacion documental curada"
    expected_version: int | None = None
    dataset_tag: str = "CURATED_BIOLINK"


@dataclass(frozen=True, slots=True)
class TaxonDossierVersionDTO:
    version_id: str
    taxon_id: str
    document_version: int
    archived_at: str
    archived_by: str | None
    change_reason: str | None
    snapshot: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class UpdateTaxonDossierResultDTO:
    taxon_id: str
    previous_version: int | None
    archived_version_id: str | None
    dossier: TaxonDossierDTO
    dossier_sync_status: str
    sync_warning: str | None = None


@dataclass(frozen=True, slots=True)
class AuditLogEntryDTO:
    audit_id: int
    entity_type: str
    entity_id: str
    action: str
    performed_by: str | None
    performed_at: str
    details: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ProvenanceHistoryResultDTO:
    entity_type: str
    entity_id: str
    current_state: dict[str, Any]
    sources: list[SourceSummaryDTO]
    source_versions: list[SourceVersionDTO]
    import_batches: list[ImportBatchDTO]
    import_records: list[ImportRecordDTO]
    evidences: list[EvidenceDetailDTO]
    review_events: list[ReviewEventDTO]
    outbox_events: list[OutboxEventDTO]
    dossier_versions: list[TaxonDossierVersionDTO]
    audit_logs: list[AuditLogEntryDTO]


@dataclass(frozen=True, slots=True)
class ReconciliationReportDTO:
    outbox_processed_count: int
    outbox_failed_count: int
    graph_expected_published_count: int
    graph_projected_or_verified_count: int
    graph_removed_stale_count: int
    dossier_checked_taxa_count: int
    dossier_reconciled_count: int
    reconciled_taxon_ids: list[str]
