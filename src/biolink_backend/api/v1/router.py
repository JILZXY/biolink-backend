from __future__ import annotations

from fastapi import APIRouter

from biolink_backend.api.v1.endpoints.admin import router as admin_router
from biolink_backend.api.v1.endpoints.auth import router as auth_router
from biolink_backend.api.v1.endpoints.health import router as health_router
from biolink_backend.api.v1.endpoints.interactions import router as interactions_router
from biolink_backend.api.v1.endpoints.network import router as network_router
from biolink_backend.api.v1.endpoints.occurrences import router as occurrences_router
from biolink_backend.api.v1.endpoints.sources import router as sources_router
from biolink_backend.api.v1.endpoints.taxa import router as taxa_router

api_v1_router = APIRouter()
api_v1_router.include_router(health_router)
api_v1_router.include_router(auth_router)
api_v1_router.include_router(taxa_router)
api_v1_router.include_router(interactions_router)
api_v1_router.include_router(network_router)
api_v1_router.include_router(sources_router)
api_v1_router.include_router(occurrences_router)
api_v1_router.include_router(admin_router)
