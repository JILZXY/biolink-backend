from __future__ import annotations

from dataclasses import asdict
from typing import Annotated

from fastapi import APIRouter, Depends

from biolink_backend.api.dependencies import get_public_query_service
from biolink_backend.api.v1.schemas.public_queries import InteractionDetailResponse
from biolink_backend.application.services.public_query_service import PublicQueryService

router = APIRouter(prefix="/interactions", tags=["Interactions"])


@router.get(
    "/{assertion_id}",
    response_model=InteractionDetailResponse,
    summary="OP-04: Consultar detalle y evidencia de interaccion",
    description="Expone evidencia, citas bibliograficas, localizadores, limites interpretativos y estado en grafo para una afirmacion PUBLISHED.",
)
def get_interaction_detail(
    assertion_id: str,
    service: Annotated[PublicQueryService, Depends(get_public_query_service)],
) -> InteractionDetailResponse:
    detail = service.get_interaction_detail(assertion_id)
    return InteractionDetailResponse.model_validate(asdict(detail))
