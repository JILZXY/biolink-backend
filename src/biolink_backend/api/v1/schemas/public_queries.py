from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class PaginationMeta(BaseModel):
    page: int
    page_size: int
    total: int
    total_pages: int


class TaxonSummarySchema(BaseModel):
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


class TaxonSearchResponse(BaseModel):
    items: list[TaxonSummarySchema]
    pagination: PaginationMeta
    applied_filters: dict[str, Any] = Field(default_factory=dict)


class TaxonNameSchema(BaseModel):
    taxon_name_id: str
    name: str
    name_usage: str
    language: str
    source_id: str
    is_preferred: bool
    verbatim_name: str | None = None


class TaxonIdentifierSchema(BaseModel):
    taxon_identifier_id: str
    scheme: str
    external_id: str
    source_id: str


class TaxonDossierClaimSchema(BaseModel):
    claim_id: str
    field: str
    value: Any
    source_id: str
    source_locator: str


class TaxonDossierMediaSchema(BaseModel):
    media_id: str
    media_type: str
    uri: str
    license: str
    attribution: str
    source_ref: str
    checksum: str | None = None


class TaxonDossierSchema(BaseModel):
    taxon_id: str
    schema_version: int
    document_version: int
    history_versions_count: int
    summary_description: str | None = None
    summary_source_refs: list[str]
    summary_locators: list[str]
    sections: dict[str, Any]
    claims: list[TaxonDossierClaimSchema]
    media: list[TaxonDossierMediaSchema]
    updated_at: str


class TaxonDetailResponse(BaseModel):
    taxon: TaxonSummarySchema
    names: list[TaxonNameSchema]
    external_identifiers: list[TaxonIdentifierSchema]
    has_dossier: bool
    dossier: TaxonDossierSchema | None = None
    warnings: list[str] = Field(default_factory=list)


class LocalitySummarySchema(BaseModel):
    locality_id: str
    name: str
    locality_type: str
    protected_status: str
    spatial_validation_status: str
    coordinate_precision: str | None = None
    parent_locality_id: str | None = None


class SourceSummarySchema(BaseModel):
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


class InteractionListItemSchema(BaseModel):
    assertion_id: str
    status: str
    interaction_type_code: str
    actor_taxon: TaxonSummarySchema
    target_taxon: TaxonSummarySchema
    locality: LocalitySummarySchema
    period_start: str
    period_end: str
    season_label: str
    method_code: str
    assertion_scope: str
    source_citations: list[str]
    source_locators: list[str]
    interpretation_limits: list[str]


class TaxonInteractionsResponse(BaseModel):
    taxon_id: str
    items: list[InteractionListItemSchema]
    pagination: PaginationMeta
    applied_filters: dict[str, Any]
    warnings: list[str]


class EvidenceDetailSchema(BaseModel):
    evidence_id: str
    source_id: str
    source_version_id: str | None = None
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
    review_note: str | None = None
    source: SourceSummarySchema


class RawSourceReferenceSchema(BaseModel):
    raw_document_id: str
    import_batch_id: str
    source_id: str
    external_record_id: str
    provider: str
    checksum: str
    license_id: str
    redistribution_permitted: bool
    raw_payload: dict[str, Any] | None = None


class StudyDossierSchema(BaseModel):
    dossier_id: str
    source_id: str
    schema_version: int
    doi_or_uri: str | None = None
    research_question: str
    sampling_design: dict[str, Any]
    study_localities: list[str]
    study_period: dict[str, Any]
    focal_taxa: list[str]
    interpretation_limits: list[str]
    license_and_usage: dict[str, Any]
    checksum: str | None = None


class InteractionDetailResponse(BaseModel):
    assertion: InteractionListItemSchema
    evidences: list[EvidenceDetailSchema]
    study_dossiers: list[StudyDossierSchema]
    raw_source_references: list[RawSourceReferenceSchema]
    graph_projected: bool
    warnings: list[str]


class SubgraphNodeSchema(BaseModel):
    node_id: str
    node_type: str
    label: str
    properties: dict[str, Any]


class SubgraphEdgeSchema(BaseModel):
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


class SubgraphResponse(BaseModel):
    applied_filters: dict[str, Any]
    nodes: list[SubgraphNodeSchema]
    interactions: list[SubgraphEdgeSchema]
    undetermined_locality_excluded_count: int
    warnings: list[str]


class LocalityNetworkMetricsSchema(BaseModel):
    locality: LocalitySummarySchema
    published_assertions_count: int
    distinct_taxa_count: int
    distinct_animal_taxa_count: int
    distinct_plant_taxa_count: int
    source_ids: list[str]


class SharedInteractionPairSchema(BaseModel):
    actor_taxon_id: str
    actor_name: str
    target_taxon_id: str
    target_name: str
    interaction_type_code: str


class LocalityComparisonResponse(BaseModel):
    locality_a: LocalityNetworkMetricsSchema
    locality_b: LocalityNetworkMetricsSchema
    shared_taxa: list[TaxonSummarySchema]
    exclusive_to_a_taxa: list[TaxonSummarySchema]
    exclusive_to_b_taxa: list[TaxonSummarySchema]
    shared_interaction_pairs: list[SharedInteractionPairSchema]
    undetermined_locality_assertions_count: int
    applied_filters: dict[str, Any]
    warnings: list[str]


class PartnerConnectivitySchema(BaseModel):
    taxon: TaxonSummarySchema
    role_of_partner: str
    assertions_count: int
    interaction_type_codes: list[str]
    locality_ids: list[str]


class ConnectivityResponse(BaseModel):
    taxon: TaxonSummarySchema
    distinct_partner_taxa_count: int
    documented_assertions_count: int
    as_actor_distinct_partners: int
    as_target_distinct_partners: int
    partners: list[PartnerConnectivitySchema]
    applied_filters: dict[str, Any]
    warnings: list[str]


class SourceVersionSchema(BaseModel):
    source_version_id: str
    provider_version: str
    release_date: str | None = None
    retrieved_at: str
    endpoint_or_file: str
    query_parameters: dict[str, Any]
    filters: dict[str, Any]
    license_id: str
    checksum: str


class SourceDetailResponse(BaseModel):
    source: SourceSummarySchema
    versions: list[SourceVersionSchema]
    published_assertions_count: int
    evidences_count: int
    has_study_dossier: bool
    study_dossier: StudyDossierSchema | None = None
    warnings: list[str]


class OccurrenceItemSchema(BaseModel):
    occurrence_id: str
    taxon: TaxonSummarySchema
    source: SourceSummarySchema
    locality: LocalitySummarySchema | None = None
    source_record_id: str | None = None
    event_date: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    coordinate_uncertainty_meters: float | None = None
    basis_of_record: str
    georeference_status: str
    is_interaction_evidence: bool
    raw_source_reference: RawSourceReferenceSchema | None = None


class OccurrenceListResponse(BaseModel):
    items: list[OccurrenceItemSchema]
    pagination: PaginationMeta
    applied_filters: dict[str, Any]
    warnings: list[str]
