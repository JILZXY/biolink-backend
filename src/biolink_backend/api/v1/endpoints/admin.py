from __future__ import annotations

from dataclasses import asdict
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, status

from biolink_backend.api.dependencies import get_admin_curation_service, require_roles
from biolink_backend.api.v1.schemas.admin_curation import (
    AdminAssertionResponseSchema,
    CreateAssertionRequestSchema,
    ImportBatchCreateRequestSchema,
    ImportBatchDetailResponseSchema,
    ImportRecordResponseSchema,
    NormalizeImportRecordRequestSchema,
    NormalizeImportRecordResponseSchema,
    ProvenanceHistoryResponseSchema,
    ReconciliationResponseSchema,
    ReviewAssertionRequestSchema,
    ReviewAssertionResponseSchema,
    SubmitAssertionRequestSchema,
    UpdateTaxonDossierRequestSchema,
    UpdateTaxonDossierResponseSchema,
)
from biolink_backend.application.dto import (
    AssertionEvidenceInputDTO,
    CreateAssertionInputDTO,
    DossierClaimInputDTO,
    DossierMediaInputDTO,
    ImportBatchCreateInputDTO,
    ImportRecordInputDTO,
    NormalizeImportRecordInputDTO,
    UpdateTaxonDossierInputDTO,
)
from biolink_backend.application.services.admin_curation_service import AdminCurationService
from biolink_backend.domain.vocabularies import ImportRecordValidationStatus, RoleCode
from biolink_backend.security.rbac import AuthenticatedUser

router = APIRouter(prefix="/admin", tags=["Administration & Curation"])


@router.post(
    "/import-batches",
    response_model=ImportBatchDetailResponseSchema,
    status_code=status.HTTP_201_CREATED,
    summary="OP-09: Registrar lote de importacion con preservacion cruda inmutable",
    description="Exclusivo CURATOR. Crea source_version, import_batch y raw_source_record sin autopublicar.",
)
def register_import_batch(
    payload: ImportBatchCreateRequestSchema,
    user: Annotated[AuthenticatedUser, Depends(require_roles(RoleCode.CURATOR))],
    service: Annotated[AdminCurationService, Depends(get_admin_curation_service)],
) -> ImportBatchDetailResponseSchema:
    dto = ImportBatchCreateInputDTO(
        source_id=payload.source_id,
        provider_version=payload.provider_version,
        endpoint_or_file=payload.endpoint_or_file,
        provider=payload.provider,
        query_parameters=payload.query_parameters,
        filters=payload.filters,
        license_id=payload.license_id,
        release_date=payload.release_date,
        auto_publish=payload.auto_publish,
        records=[
            ImportRecordInputDTO(
                external_record_id=rec.external_record_id,
                raw_payload=rec.raw_payload,
                verbatim_name=rec.verbatim_name,
                proposed_external_scheme=rec.proposed_external_scheme.value if rec.proposed_external_scheme else None,
                proposed_external_id=rec.proposed_external_id,
                redistribution_permitted=rec.redistribution_permitted,
                full_text_stored=rec.full_text_stored,
            )
            for rec in payload.records
        ],
    )
    result = service.register_import_batch(user=user, data=dto)
    return ImportBatchDetailResponseSchema.model_validate(asdict(result))


@router.get(
    "/import-batches/{import_batch_id}",
    response_model=ImportBatchDetailResponseSchema,
    summary="Consultar detalle de lote de importacion y sus registros",
)
def get_import_batch_detail(
    import_batch_id: str,
    user: Annotated[AuthenticatedUser, Depends(require_roles(RoleCode.CURATOR, RoleCode.REVIEWER, RoleCode.ADMIN))],
    service: Annotated[AdminCurationService, Depends(get_admin_curation_service)],
) -> ImportBatchDetailResponseSchema:
    result = service.get_import_batch_detail(user=user, import_batch_id=import_batch_id)
    return ImportBatchDetailResponseSchema.model_validate(asdict(result))


@router.get(
    "/import-records",
    response_model=list[ImportRecordResponseSchema],
    summary="Listar registros importados filtrables para revision y normalizacion (p. ej. UNRESOLVED)",
)
def list_import_records(
    user: Annotated[AuthenticatedUser, Depends(require_roles(RoleCode.CURATOR, RoleCode.REVIEWER, RoleCode.ADMIN))],
    service: Annotated[AdminCurationService, Depends(get_admin_curation_service)],
    import_batch_id: str | None = Query(default=None),
    validation_status: ImportRecordValidationStatus | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
) -> list[ImportRecordResponseSchema]:
    items = service.list_import_records(
        user=user,
        import_batch_id=import_batch_id,
        validation_status=validation_status.value if validation_status else None,
        limit=limit,
    )
    return [ImportRecordResponseSchema.model_validate(asdict(item)) for item in items]


@router.patch(
    "/import-records/{import_record_id}/normalize",
    response_model=NormalizeImportRecordResponseSchema,
    summary="OP-10: Validar y normalizar registro importado",
    description="Exclusivo CURATOR. Registra nombre original, taxón canonico, ID externo, regla y estado sin mutar raw_payload.",
)
def normalize_import_record(
    import_record_id: str,
    payload: NormalizeImportRecordRequestSchema,
    user: Annotated[AuthenticatedUser, Depends(require_roles(RoleCode.CURATOR))],
    service: Annotated[AdminCurationService, Depends(get_admin_curation_service)],
) -> NormalizeImportRecordResponseSchema:
    dto = NormalizeImportRecordInputDTO(
        import_record_id=import_record_id,
        validation_status=payload.validation_status,
        matching_rule=payload.matching_rule,
        canonical_taxon_id=payload.canonical_taxon_id,
        verbatim_name=payload.verbatim_name,
        external_scheme=payload.external_scheme.value if payload.external_scheme else None,
        external_id=payload.external_id,
        validation_notes=payload.validation_notes,
        register_name_variant=payload.register_name_variant,
        register_external_identifier=payload.register_external_identifier,
    )
    result = service.normalize_import_record(user=user, data=dto)
    return NormalizeImportRecordResponseSchema.model_validate(asdict(result))


@router.post(
    "/assertions",
    response_model=AdminAssertionResponseSchema,
    status_code=status.HTTP_201_CREATED,
    summary="OP-11: Crear afirmacion de interaccion con evidencia explicita",
    description="Exclusivo CURATOR. Crea afirmacion en DRAFT o IN_REVIEW respetando RD-01..RD-06.",
)
def create_assertion(
    payload: CreateAssertionRequestSchema,
    user: Annotated[AuthenticatedUser, Depends(require_roles(RoleCode.CURATOR))],
    service: Annotated[AdminCurationService, Depends(get_admin_curation_service)],
) -> AdminAssertionResponseSchema:
    dto = CreateAssertionInputDTO(
        actor_taxon_id=payload.actor_taxon_id,
        target_taxon_id=payload.target_taxon_id,
        interaction_type_code=payload.interaction_type_code.value,
        locality_id=payload.locality_id,
        period_start=payload.period_start,
        period_end=payload.period_end,
        season_label=payload.season_label,
        method_code=payload.method_code.value,
        assertion_scope=payload.assertion_scope,
        initial_status=payload.initial_status,
        submission_reason=payload.submission_reason,
        evidences=[
            AssertionEvidenceInputDTO(
                evidence_role=ev.evidence_role,
                review_note=ev.review_note,
                evidence_id=ev.evidence_id,
                source_id=ev.source_id,
                source_version_id=ev.source_version_id,
                source_locator=ev.source_locator,
                evidence_kind=ev.evidence_kind.value if ev.evidence_kind else None,
                method_code=ev.method_code.value if ev.method_code else None,
                reported_term=ev.reported_term,
                summary=ev.summary,
                interpretation_limit=ev.interpretation_limit,
                period_start=ev.period_start,
                period_end=ev.period_end,
                license_id=ev.license_id,
            )
            for ev in payload.evidences
        ],
    )
    result = service.create_assertion(user=user, data=dto)
    return AdminAssertionResponseSchema.model_validate(asdict(result))


@router.get(
    "/assertions/{assertion_id}",
    response_model=AdminAssertionResponseSchema,
    summary="Consultar detalle administrativo de una afirmacion en cualquier estado",
)
def get_admin_assertion_detail(
    assertion_id: str,
    user: Annotated[AuthenticatedUser, Depends(require_roles(RoleCode.CURATOR, RoleCode.REVIEWER, RoleCode.ADMIN))],
    service: Annotated[AdminCurationService, Depends(get_admin_curation_service)],
) -> AdminAssertionResponseSchema:
    result = service.get_assertion_detail(user=user, assertion_id=assertion_id)
    return AdminAssertionResponseSchema.model_validate(asdict(result))


@router.post(
    "/assertions/{assertion_id}/submit",
    response_model=ReviewAssertionResponseSchema,
    summary="OP-11/OP-12: Preparar y enviar afirmacion DRAFT a revision (IN_REVIEW)",
    description="Exclusivo CURATOR. Transiciona DRAFT -> IN_REVIEW sin publicar ni proyectar en Neo4j.",
)
def submit_assertion_for_review(
    assertion_id: str,
    payload: SubmitAssertionRequestSchema,
    user: Annotated[AuthenticatedUser, Depends(require_roles(RoleCode.CURATOR))],
    service: Annotated[AdminCurationService, Depends(get_admin_curation_service)],
) -> ReviewAssertionResponseSchema:
    result = service.submit_assertion_for_review(
        user=user,
        assertion_id=assertion_id,
        reason=payload.reason,
    )
    return ReviewAssertionResponseSchema.model_validate(asdict(result))


@router.post(
    "/assertions/{assertion_id}/review",
    response_model=ReviewAssertionResponseSchema,
    summary="OP-12: Aprobar, rechazar, retirar o reabrir afirmacion (Outbox ACID)",
    description="Exclusivo REVIEWER. En aprobacion o retiro desde PUBLISHED inserta outbox_event en la misma transaccion ACID.",
)
def review_assertion(
    assertion_id: str,
    payload: ReviewAssertionRequestSchema,
    user: Annotated[AuthenticatedUser, Depends(require_roles(RoleCode.REVIEWER))],
    service: Annotated[AdminCurationService, Depends(get_admin_curation_service)],
) -> ReviewAssertionResponseSchema:
    result = service.review_assertion(
        user=user,
        assertion_id=assertion_id,
        action=payload.action,
        reason=payload.reason,
    )
    return ReviewAssertionResponseSchema.model_validate(asdict(result))


@router.put(
    "/taxa/{taxon_id}/dossier",
    response_model=UpdateTaxonDossierResponseSchema,
    summary="OP-13: Actualizar ficha documental versionada de un taxon",
    description="Exclusivo CURATOR. Archiva la version previa en taxon_dossier_versions, incrementa version y registra auditoria.",
)
def update_taxon_dossier(
    taxon_id: str,
    payload: UpdateTaxonDossierRequestSchema,
    user: Annotated[AuthenticatedUser, Depends(require_roles(RoleCode.CURATOR))],
    service: Annotated[AdminCurationService, Depends(get_admin_curation_service)],
) -> UpdateTaxonDossierResponseSchema:
    dto = UpdateTaxonDossierInputDTO(
        summary_description=payload.summary_description,
        summary_source_refs=payload.summary_source_refs,
        summary_locators=payload.summary_locators,
        sections=payload.sections,
        claims=[
            DossierClaimInputDTO(
                claim_id=c.claim_id,
                field=c.field,
                value=c.value,
                source_id=c.source_id,
                source_locator=c.source_locator,
            )
            for c in payload.claims
        ],
        media=[
            DossierMediaInputDTO(
                media_id=m.media_id,
                media_type=m.media_type,
                uri=m.uri,
                license=m.license,
                attribution=m.attribution,
                source_ref=m.source_ref,
                checksum=m.checksum,
            )
            for m in payload.media
        ],
        change_reason=payload.change_reason,
        expected_version=payload.expected_version,
        dataset_tag=payload.dataset_tag,
    )
    result = service.update_taxon_dossier(user=user, taxon_id=taxon_id, data=dto)
    return UpdateTaxonDossierResponseSchema.model_validate(asdict(result))


@router.get(
    "/provenance/{entity_type}/{entity_id}",
    response_model=ProvenanceHistoryResponseSchema,
    summary="OP-14: Consultar historial completo de procedencia y auditoria",
    description="Accesible para CURATOR y REVIEWER. Reconstruye fuentes, lotes, revisiones, outbox y versiones documentales.",
)
def get_provenance_history(
    entity_type: Literal["assertion", "taxon", "import_batch", "import_record"],
    entity_id: str,
    user: Annotated[AuthenticatedUser, Depends(require_roles(RoleCode.CURATOR, RoleCode.REVIEWER, RoleCode.ADMIN))],
    service: Annotated[AdminCurationService, Depends(get_admin_curation_service)],
) -> ProvenanceHistoryResponseSchema:
    result = service.get_provenance_history(
        user=user,
        entity_type=entity_type,
        entity_id=entity_id,
    )
    return ProvenanceHistoryResponseSchema.model_validate(asdict(result))


@router.post(
    "/reconcile",
    response_model=ReconciliationResponseSchema,
    summary="Reconciliar proyecciones Neo4j y versiones documentales MongoDB desde PostgreSQL",
    description="Exclusivo REVIEWER o ADMIN. Reencola eventos fallidos del Outbox y sincroniza estado faltante o huerfano.",
)
def reconcile_multi_model_state(
    user: Annotated[AuthenticatedUser, Depends(require_roles(RoleCode.REVIEWER, RoleCode.ADMIN))],
    service: Annotated[AdminCurationService, Depends(get_admin_curation_service)],
) -> ReconciliationResponseSchema:
    result = service.reconcile_system_state(user=user)
    return ReconciliationResponseSchema.model_validate(asdict(result))
