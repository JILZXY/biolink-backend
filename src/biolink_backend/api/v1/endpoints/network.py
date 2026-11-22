from __future__ import annotations

from dataclasses import asdict
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from biolink_backend.api.dependencies import get_public_query_service
from biolink_backend.api.v1.schemas.public_queries import (
    LocalityComparisonResponse,
    SubgraphResponse,
)
from biolink_backend.application.services.public_query_service import PublicQueryService

router = APIRouter(prefix="/network", tags=["Network"])


@router.get(
    "/subgraph",
    response_model=SubgraphResponse,
    summary="OP-05: Filtrar subred documentada",
    description="Construye subred ecologica filtrada sobre afirmaciones PUBLISHED sin mezclar localidades externas ni indeterminadas.",
)
def filter_documented_subgraph(
    service: Annotated[PublicQueryService, Depends(get_public_query_service)],
    taxon_id: str | None = Query(default=None),
    locality_id: str | None = Query(default=None),
    protected_status: str | None = Query(default=None),
    spatial_validation_status: str | None = Query(default=None),
    interaction_type_code: str | None = Query(default=None),
    method_code: str | None = Query(default=None),
    source_id: str | None = Query(default=None),
    period_start: str | None = Query(default=None),
    period_end: str | None = Query(default=None),
    max_depth: int = Query(default=1, ge=1, le=3),
    limit: int = Query(default=100, ge=1, le=200),
) -> SubgraphResponse:
    res = service.filter_documented_subgraph(
        taxon_id=taxon_id,
        locality_id=locality_id,
        protected_status=protected_status,
        spatial_validation_status=spatial_validation_status,
        interaction_type_code=interaction_type_code,
        method_code=method_code,
        source_id=source_id,
        period_start=period_start,
        period_end=period_end,
        max_depth=max_depth,
        limit=limit,
    )
    return SubgraphResponse.model_validate(asdict(res))


@router.get(
    "/compare-localities",
    response_model=LocalityComparisonResponse,
    summary="OP-06: Comparar localidades",
    description="Compara redes documentadas de dos localidades distintas (ej. Montes Azules vs Playon de la Gloria) manteniendo contextos separados.",
)
def compare_localities(
    service: Annotated[PublicQueryService, Depends(get_public_query_service)],
    locality_id_a: str = Query(..., description="Identificador de la primera localidad"),
    locality_id_b: str = Query(..., description="Identificador de la segunda localidad"),
    interaction_type_code: str | None = Query(default=None),
    period_start: str | None = Query(default=None),
    period_end: str | None = Query(default=None),
) -> LocalityComparisonResponse:
    res = service.compare_localities(
        locality_id_a=locality_id_a,
        locality_id_b=locality_id_b,
        interaction_type_code=interaction_type_code,
        period_start=period_start,
        period_end=period_end,
    )
    return LocalityComparisonResponse.model_validate(asdict(res))
