from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse

from biolink_backend.api.dependencies import get_health_service
from biolink_backend.api.v1.schemas.health import (
    ConsolidatedHealthResponse,
    EngineHealthSchema,
    LivenessResponse,
    ReadinessResponse,
)
from biolink_backend.application.ports import EngineHealthStatus
from biolink_backend.application.services.health_service import HealthService

router = APIRouter(prefix="/health", tags=["Health"])


def _map_dependencies(deps: dict[str, EngineHealthStatus]) -> dict[str, EngineHealthSchema]:
    return {
        name: EngineHealthSchema(
            healthy=item.healthy,
            latency_ms=item.latency_ms,
            version=item.version,
            error=item.error,
        )
        for name, item in deps.items()
    }


@router.get(
    "/live",
    response_model=LivenessResponse,
    status_code=status.HTTP_200_OK,
    summary="Liveness probe del proceso API",
    description="Confirma que el proceso FastAPI esta activo y atendiendo solicitudes sin consultar las bases de datos.",
)
def health_live(
    service: Annotated[HealthService, Depends(get_health_service)],
) -> LivenessResponse:
    report = service.check_liveness()
    return LivenessResponse(
        status="alive",
        service=report.service,
        version=report.version,
        environment=report.environment,
        uptime_seconds=report.uptime_seconds,
        timestamp_utc=report.timestamp_utc,
    )


@router.get(
    "/ready",
    response_model=ReadinessResponse,
    responses={
        503: {
            "model": ReadinessResponse,
            "description": "Una o mas bases de datos requeridas (PostgreSQL, MongoDB o Neo4j) no estan disponibles.",
        }
    },
    summary="Readiness probe hacia PostgreSQL, MongoDB y Neo4j",
    description="Verifica conectividad real contra los tres motores definitivos antes de aceptar trafico operativo.",
)
def health_ready(
    service: Annotated[HealthService, Depends(get_health_service)],
) -> JSONResponse:
    report = service.check_readiness()
    payload = ReadinessResponse(
        status="ready" if report.ready else "not_ready",
        ready=report.ready,
        timestamp_utc=report.timestamp_utc,
        dependencies=_map_dependencies(report.dependencies),
    )
    code = status.HTTP_200_OK if report.ready else status.HTTP_503_SERVICE_UNAVAILABLE
    return JSONResponse(status_code=code, content=payload.model_dump(mode="json"))


@router.get(
    "",
    response_model=ConsolidatedHealthResponse,
    responses={
        503: {
            "model": ConsolidatedHealthResponse,
            "description": "El proceso esta vivo pero el estado general esta degradado por alguna dependencia.",
        }
    },
    summary="Estado general consolidado de BioLink Backend",
    description="Combina liveness del proceso, readiness real de los tres motores y presupuesto de memoria configurado.",
)
def health_consolidated(
    service: Annotated[HealthService, Depends(get_health_service)],
) -> JSONResponse:
    report = service.check_consolidated()
    payload = ConsolidatedHealthResponse(
        status="ok" if report.ready else "degraded",
        live=report.live,
        ready=report.ready,
        service=report.service,
        version=report.version,
        environment=report.environment,
        uptime_seconds=report.uptime_seconds,
        timestamp_utc=report.timestamp_utc,
        memory_budget_mb=report.memory_budget_mb,
        dependencies=_map_dependencies(report.dependencies),
    )
    code = status.HTTP_200_OK if report.ready else status.HTTP_503_SERVICE_UNAVAILABLE
    return JSONResponse(status_code=code, content=payload.model_dump(mode="json"))
