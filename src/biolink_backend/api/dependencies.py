from __future__ import annotations

from collections.abc import Callable
from typing import Annotated

from fastapi import Depends, Request

from biolink_backend.application.services.admin_curation_service import AdminCurationService
from biolink_backend.application.services.auth_service import AuthService
from biolink_backend.application.services.health_service import HealthService
from biolink_backend.application.services.public_query_service import PublicQueryService
from biolink_backend.config.settings import Settings
from biolink_backend.domain.vocabularies import RoleCode
from biolink_backend.infrastructure.manager import DatabaseConnectionManager
from biolink_backend.repositories.document.admin_dossier_repository import MongoAdminDossierRepository
from biolink_backend.repositories.document.public_dossier_repository import MongoPublicDossierRepository
from biolink_backend.repositories.graph.projection_repository import Neo4jProjectionRepository
from biolink_backend.repositories.graph.public_network_repository import Neo4jPublicNetworkRepository
from biolink_backend.repositories.relational.admin_curation_repository import PostgresAdminCurationRepository
from biolink_backend.repositories.relational.auth_repository import PostgresAuthRepository
from biolink_backend.repositories.relational.outbox_repository import PostgresOutboxRepository
from biolink_backend.repositories.relational.public_catalog_repository import PostgresPublicCatalogRepository
from biolink_backend.security.rbac import AuthenticatedUser, ensure_has_any_role


def get_app_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_connection_manager(request: Request) -> DatabaseConnectionManager:
    return request.app.state.db_manager


def get_health_service(request: Request) -> HealthService:
    return request.app.state.health_service


def get_auth_service(
    settings: Annotated[Settings, Depends(get_app_settings)],
    db_manager: Annotated[DatabaseConnectionManager, Depends(get_connection_manager)],
) -> AuthService:
    return AuthService(
        repository=PostgresAuthRepository(db_manager.postgres),
        settings=settings,
    )


def get_public_query_service(
    db_manager: Annotated[DatabaseConnectionManager, Depends(get_connection_manager)],
) -> PublicQueryService:
    return PublicQueryService(
        relational=PostgresPublicCatalogRepository(db_manager.postgres),
        document=MongoPublicDossierRepository(db_manager.mongodb),
        graph=Neo4jPublicNetworkRepository(db_manager.neo4j),
    )


def get_admin_curation_service(
    db_manager: Annotated[DatabaseConnectionManager, Depends(get_connection_manager)],
) -> AdminCurationService:
    return AdminCurationService(
        admin_relational=PostgresAdminCurationRepository(db_manager.postgres),
        admin_document=MongoAdminDossierRepository(db_manager.mongodb),
        outbox_repo=PostgresOutboxRepository(db_manager.postgres),
        graph_projection=Neo4jProjectionRepository(db_manager.neo4j),
    )


def extract_session_token(request: Request, settings: Settings) -> str | None:
    """Extrae token desde header Authorization Bearer o cookie HttpOnly (nunca localStorage)."""
    auth_header = request.headers.get("Authorization", "")
    if auth_header.lower().startswith("bearer "):
        bearer_token = auth_header[7:].strip()
        if bearer_token:
            return bearer_token

    cookie_token = request.cookies.get(settings.session_cookie_name)
    if cookie_token and cookie_token.strip():
        return cookie_token.strip()
    return None


def get_current_user(
    request: Request,
    settings: Annotated[Settings, Depends(get_app_settings)],
    auth_service: Annotated[AuthService, Depends(get_auth_service)],
) -> AuthenticatedUser:
    raw_token = extract_session_token(request, settings)
    return auth_service.resolve_session(raw_token)


def require_roles(*allowed_roles: RoleCode) -> Callable[[AuthenticatedUser], AuthenticatedUser]:
    """Dependencia FastAPI para exigir roles internos autorizados (Curador / Revisor)."""

    def _dependency(
        user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    ) -> AuthenticatedUser:
        return ensure_has_any_role(user, allowed_roles)

    return _dependency
