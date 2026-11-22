from __future__ import annotations

import math
from dataclasses import asdict
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query

from biolink_backend.api.dependencies import get_public_query_service
from biolink_backend.api.v1.schemas.public_queries import (
    OccurrenceItemSchema,
    OccurrenceListResponse,
    PaginationMeta,
)
from biolink_backend.application.dto import DISCLAIMER_OCCURRENCE_NOT_INTERACTION
from biolink_backend.application.services.public_query_service import PublicQueryService

router = APIRouter(prefix="/occurrences", tags=["Occurrences"])


@router.get(
    "",
    response_model=OccurrenceListResponse,
    summary="OP-15: Consultar ocurrencias",
    description="Lista registros de presencia separados de interacciones ecologicas, preservando incertidumbre y procedencia.",
)
def list_occurrences(
    service: Annotated[PublicQueryService, Depends(get_public_query_service)],
    taxon_id: str | None = Query(default=None),
    locality_id: str | None = Query(default=None),
    source_id: str | None = Query(default=None),
    georeference_status: str | None = Query(default=None),
    date_from: str | None = Query(default=None),
    date_to: str | None = Query(default=None),
    include_raw_reference: bool = Query(default=False),
    sort_by: Literal["occurrence_id", "event_date", "accepted_name"] = Query(default="occurrence_id"),
    sort_order: Literal["asc", "desc"] = Query(default="asc"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> OccurrenceListResponse:
    res = service.list_occurrences(
        taxon_id=taxon_id,
        locality_id=locality_id,
        source_id=source_id,
        georeference_status=georeference_status,
        date_from=date_from,
        date_to=date_to,
        include_raw_reference=include_raw_reference,
        sort_by=sort_by,
        sort_order=sort_order,
        page=page,
        page_size=page_size,
    )
    total_pages = math.ceil(res.total / res.page_size) if res.total > 0 else 0
    applied = {
        k: v
        for k, v in {
            "taxon_id": taxon_id,
            "locality_id": locality_id,
            "source_id": source_id,
            "georeference_status": georeference_status,
            "date_from": date_from,
            "date_to": date_to,
            "sort_by": sort_by,
            "sort_order": sort_order,
        }.items()
        if v is not None
    }
    return OccurrenceListResponse(
        items=[OccurrenceItemSchema.model_validate(asdict(item)) for item in res.items],
        pagination=PaginationMeta(
            page=res.page,
            page_size=res.page_size,
            total=res.total,
            total_pages=total_pages,
        ),
        applied_filters=applied,
        warnings=[DISCLAIMER_OCCURRENCE_NOT_INTERACTION],
    )
