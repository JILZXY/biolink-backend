from __future__ import annotations

from dataclasses import asdict
from typing import Annotated

from fastapi import APIRouter, Depends

from biolink_backend.api.dependencies import get_public_query_service
from biolink_backend.api.v1.schemas.public_queries import SourceDetailResponse
from biolink_backend.application.services.public_query_service import PublicQueryService

router = APIRouter(prefix="/sources", tags=["Sources"])


@router.get(
    "/{source_id}",
    response_model=SourceDetailResponse,
    summary="OP-08: Consultar fuente o estudio",
    description="Devuelve cita bibliografica, versiones de dataset, licencias y expediente metodologico documental.",
)
def get_source_detail(
    source_id: str,
    service: Annotated[PublicQueryService, Depends(get_public_query_service)],
) -> SourceDetailResponse:
    detail = service.get_source_detail(source_id)
    return SourceDetailResponse.model_validate(asdict(detail))
