from __future__ import annotations

import math
from dataclasses import asdict
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query

from biolink_backend.api.dependencies import get_public_query_service
from biolink_backend.api.v1.schemas.public_queries import (
    ConnectivityResponse,
    InteractionListItemSchema,
    PaginationMeta,
    TaxonDetailResponse,
    TaxonInteractionsResponse,
    TaxonSearchResponse,
    TaxonSummarySchema,
)
from biolink_backend.application.dto import (
    DISCLAIMER_FRUGIVORY_NO_DISPERSAL,
    DISCLAIMER_LOCALITY_SEPARATION,
)
from biolink_backend.application.services.public_query_service import PublicQueryService

router = APIRouter(prefix="/taxa", tags=["Taxa"])


def _pagination_meta(total: int, page: int, page_size: int) -> PaginationMeta:
    total_pages = math.ceil(total / page_size) if total > 0 else 0
    return PaginationMeta(page=page, page_size=page_size, total=total, total_pages=total_pages)


@router.get(
    "",
    response_model=TaxonSearchResponse,
    summary="OP-01: Buscar taxones",
    description="Busca taxones por nombre aceptado, sinonimo, grafia original o identificador externo con paginacion.",
)
def search_taxa(
    service: Annotated[PublicQueryService, Depends(get_public_query_service)],
    q: str | None = Query(default=None, min_length=1, max_length=120, description="Nombre cientifico, sinonimo o ID externo"),
    taxonomic_rank: str | None = Query(default=None, description="SPECIES, GENUS, MORPHOSPECIES, etc."),
    organism_group: str | None = Query(default=None, description="ANIMAL o PLANT"),
    taxonomic_status: str | None = Query(default=None, description="ACCEPTED, UNRESOLVED_TO_SPECIES, NEEDS_RECONCILIATION, etc."),
    sort_by: Literal["accepted_name", "taxon_id", "taxonomic_rank"] = Query(default="accepted_name"),
    sort_order: Literal["asc", "desc"] = Query(default="asc"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> TaxonSearchResponse:
    res = service.search_taxa(
        query=q,
        taxonomic_rank=taxonomic_rank,
        organism_group=organism_group,
        taxonomic_status=taxonomic_status,
        sort_by=sort_by,
        sort_order=sort_order,
        page=page,
        page_size=page_size,
    )
    applied = {
        k: v
        for k, v in {
            "q": q,
            "taxonomic_rank": taxonomic_rank,
            "organism_group": organism_group,
            "taxonomic_status": taxonomic_status,
            "sort_by": sort_by,
            "sort_order": sort_order,
        }.items()
        if v is not None
    }
    return TaxonSearchResponse(
        items=[TaxonSummarySchema.model_validate(asdict(item)) for item in res.items],
        pagination=_pagination_meta(res.total, res.page, res.page_size),
        applied_filters=applied,
    )


@router.get(
    "/{taxon_id}",
    response_model=TaxonDetailResponse,
    summary="OP-02: Consultar ficha de taxon",
    description="Integra la identidad taxonomica canonica relacional con el expediente documental en MongoDB.",
)
def get_taxon_fiche(
    taxon_id: str,
    service: Annotated[PublicQueryService, Depends(get_public_query_service)],
) -> TaxonDetailResponse:
    detail = service.get_taxon_fiche(taxon_id)
    return TaxonDetailResponse.model_validate(asdict(detail))


@router.get(
    "/{taxon_id}/interactions",
    response_model=TaxonInteractionsResponse,
    summary="OP-03: Listar interacciones de un taxon",
    description="Lista afirmaciones PUBLISHED documentadas donde participa el taxon, excluyendo borradores o retiradas.",
)
def list_taxon_interactions(
    taxon_id: str,
    service: Annotated[PublicQueryService, Depends(get_public_query_service)],
    role: Literal["any", "actor", "target"] = Query(default="any"),
    interaction_type_code: str | None = Query(default=None),
    locality_id: str | None = Query(default=None),
    method_code: str | None = Query(default=None),
    period_start: str | None = Query(default=None, description="Formato ISO parcial YYYY, YYYY-MM o YYYY-MM-DD"),
    period_end: str | None = Query(default=None, description="Formato ISO parcial YYYY, YYYY-MM o YYYY-MM-DD"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> TaxonInteractionsResponse:
    res = service.list_taxon_interactions(
        taxon_id=taxon_id,
        role=role,
        interaction_type_code=interaction_type_code,
        locality_id=locality_id,
        method_code=method_code,
        period_start=period_start,
        period_end=period_end,
        page=page,
        page_size=page_size,
    )
    applied = {
        k: v
        for k, v in {
            "role": role,
            "interaction_type_code": interaction_type_code,
            "locality_id": locality_id,
            "method_code": method_code,
            "period_start": period_start,
            "period_end": period_end,
        }.items()
        if v is not None
    }
    return TaxonInteractionsResponse(
        taxon_id=taxon_id,
        items=[InteractionListItemSchema.model_validate(asdict(item)) for item in res.items],
        pagination=_pagination_meta(res.total, res.page, res.page_size),
        applied_filters=applied,
        warnings=[DISCLAIMER_FRUGIVORY_NO_DISPERSAL, DISCLAIMER_LOCALITY_SEPARATION],
    )


@router.get(
    "/{taxon_id}/connectivity",
    response_model=ConnectivityResponse,
    summary="OP-07: Calcular conectividad descriptiva",
    description="Calcula numero de taxones contraparte distintos en la red documentada (no equivale a abundancia).",
)
def get_taxon_connectivity(
    taxon_id: str,
    service: Annotated[PublicQueryService, Depends(get_public_query_service)],
    locality_id: str | None = Query(default=None),
    interaction_type_code: str | None = Query(default=None),
    period_start: str | None = Query(default=None),
    period_end: str | None = Query(default=None),
) -> ConnectivityResponse:
    res = service.calculate_descriptive_connectivity(
        taxon_id=taxon_id,
        locality_id=locality_id,
        interaction_type_code=interaction_type_code,
        period_start=period_start,
        period_end=period_end,
    )
    return ConnectivityResponse.model_validate(asdict(res))
