from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from biolink_backend.domain.value_objects import PartialDateRange
from biolink_backend.domain.vocabularies import (
    AssertionStatus,
    EvidenceKind,
    EvidenceMethodCode,
    InteractionTypeCode,
    LocalityType,
    OrganismGroup,
    OutboxEventType,
    OutboxStatus,
    ProtectedStatus,
    RoleCode,
    SpatialValidationStatus,
    TaxonomicRank,
    TaxonomicStatus,
)


@dataclass(frozen=True, slots=True)
class Taxon:
    taxon_id: str
    accepted_name: str
    scientific_authorship: str
    taxonomic_rank: TaxonomicRank
    organism_group: OrganismGroup
    taxonomic_status: TaxonomicStatus


@dataclass(frozen=True, slots=True)
class Locality:
    locality_id: str
    name: str
    locality_type: LocalityType
    protected_status: ProtectedStatus
    spatial_validation_status: SpatialValidationStatus
    source_id: str
    parent_locality_id: str | None = None
    coordinate_precision: str | None = None


@dataclass(frozen=True, slots=True)
class Source:
    source_id: str
    source_type: str
    title: str
    authors: str
    publication_year: int
    citation_text: str
    license_id: str
    doi: str | None = None
    uri: str | None = None


@dataclass(frozen=True, slots=True)
class Evidence:
    evidence_id: str
    source_id: str
    source_locator: str
    evidence_kind: EvidenceKind
    method_code: EvidenceMethodCode
    reported_term: str
    summary: str
    interpretation_limit: str
    period: PartialDateRange
    license_id: str
    source_version_id: str | None = None


@dataclass(frozen=True, slots=True)
class InteractionAssertion:
    assertion_id: str
    actor_taxon_id: str
    target_taxon_id: str
    interaction_type_code: InteractionTypeCode
    locality_id: str
    period: PartialDateRange
    season_label: str
    method_code: EvidenceMethodCode
    assertion_scope: str
    status: AssertionStatus
    created_by: str
    created_at: datetime
    reviewed_by: str | None = None
    reviewed_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class ReviewEvent:
    review_event_id: str
    assertion_id: str
    user_id: str
    previous_status: AssertionStatus
    new_status: AssertionStatus
    reason: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class OutboxEvent:
    outbox_event_id: str
    aggregate_type: str
    aggregate_id: str
    event_type: OutboxEventType
    payload_json: dict[str, Any]
    status: OutboxStatus
    attempts: int
    created_at: datetime
    aggregate_version: int = 1
    payload_version: int = 1
    processed_at: datetime | None = None
    error_message: str | None = None

    @property
    def event_id(self) -> str:
        return self.outbox_event_id

    @property
    def assertion_id(self) -> str:
        return self.aggregate_id

    @property
    def processing_status(self) -> OutboxStatus:
        return self.status

    @property
    def last_error(self) -> str | None:
        return self.error_message


@dataclass(frozen=True, slots=True)
class InternalUser:
    user_id: str
    display_name: str
    email: str
    is_active: bool
    roles: frozenset[RoleCode] = field(default_factory=frozenset)
