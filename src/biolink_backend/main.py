from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi

from biolink_backend.api.errors import register_exception_handlers
from biolink_backend.api.middleware import RequestContextMiddleware
from biolink_backend.api.v1.router import api_v1_router
from biolink_backend.api.v1.schemas.common import ErrorResponse
from biolink_backend.application.services.health_service import HealthService
from biolink_backend.config.settings import Settings, get_settings
from biolink_backend.infrastructure.manager import DatabaseConnectionManager
from biolink_backend.repositories.document.health_repository import MongoHealthRepository
from biolink_backend.repositories.graph.health_repository import Neo4jHealthRepository
from biolink_backend.repositories.relational.health_repository import PostgresHealthRepository
from biolink_backend.shared.logging import configure_logging, get_logger

logger = get_logger(__name__)


_OPENAPI_TAGS: list[dict[str, str]] = [
    {"name": "Health", "description": "Sondas liveness/readiness y diagnóstico del presupuesto de memoria."},
    {"name": "Authentication", "description": "Sesiones stateful internas con PBKDF2-HMAC-SHA256, SHA-256 y cookies HttpOnly."},
    {"name": "Taxa", "description": "OP-01 y OP-02: Búsqueda taxonómica, sinónimos, IDs externos y expedientes taxon_dossier."},
    {"name": "Interactions", "description": "OP-03 y OP-04: Afirmaciones PUBLISHED, evidencia, citas y estado de proyección en Neo4j."},
    {"name": "Network", "description": "OP-05, OP-06 y OP-07: Subredes filtradas, comparación de localidades y conectividad."},
    {"name": "Sources", "description": "OP-08: Consulta bibliográfica, versiones de dataset y expedientes study_dossier."},
    {"name": "Occurrences", "description": "OP-15: Registros de presencia separados de afirmaciones de interacción."},
    {"name": "Administration & Curation", "description": "OP-09 a OP-14 y reconciliación multi-modelo con RBAC (Curador / Revisor / Admin)."},
]


def _build_default_health_service(settings: Settings, db_manager: DatabaseConnectionManager) -> HealthService:
    return HealthService(
        settings=settings,
        relational=PostgresHealthRepository(db_manager.postgres),
        document=MongoHealthRepository(db_manager.mongodb),
        graph=Neo4jHealthRepository(db_manager.neo4j),
    )


def create_app(
    settings: Settings | None = None,
    *,
    db_manager: DatabaseConnectionManager | None = None,
    health_service: HealthService | None = None,
) -> FastAPI:
    """Fabrica principal de la aplicacion FastAPI integrada en biolink-backend."""
    resolved_settings = settings or get_settings()
    configure_logging(resolved_settings.log_level, resolved_settings.log_format)

    manager = db_manager or DatabaseConnectionManager(resolved_settings)
    resolved_health = health_service or _build_default_health_service(resolved_settings, manager)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.settings = resolved_settings
        app.state.db_manager = manager
        app.state.health_service = resolved_health
        manager.startup()
        logger.info(
            "Aplicacion BioLink Backend iniciada",
            extra={
                "app_env": resolved_settings.app_env,
                "version": resolved_settings.app_version,
                "api_prefix": resolved_settings.api_v1_prefix,
            },
        )
        try:
            yield
        finally:
            manager.shutdown()
            logger.info("Aplicacion BioLink Backend detenida")

    app = FastAPI(
        title="BioLink API",
        version=resolved_settings.app_version,
        description=(
            "API REST integrada de biolink-backend para consultas ecologicas, "
            "curaduria cientifica y administracion sobre PostgreSQL, MongoDB y Neo4j."
        ),
        openapi_tags=_OPENAPI_TAGS,
        contact={"name": "Equipo BioLink", "url": "https://biolink.local"},
        license_info={"name": "Academic & Research Use"},
        servers=[
            {
                "url": f"http://{resolved_settings.backend_host}:{resolved_settings.backend_port}",
                "description": "Servidor local de desarrollo biolink-backend",
            }
        ],
        openapi_url=f"{resolved_settings.api_v1_prefix}/openapi.json",
        docs_url=f"{resolved_settings.api_v1_prefix}/docs",
        redoc_url=f"{resolved_settings.api_v1_prefix}/redoc",
        lifespan=lifespan,
    )

    # Inicializacion preventiva de app.state para pruebas sin contexto lifespan completo si aplica
    app.state.settings = resolved_settings
    app.state.db_manager = manager
    app.state.health_service = resolved_health

    app.add_middleware(
        CORSMiddleware,
        allow_origins=resolved_settings.cors_allowed_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
    )
    app.add_middleware(RequestContextMiddleware)

    register_exception_handlers(app)
    app.include_router(api_v1_router, prefix=resolved_settings.api_v1_prefix)

    def custom_openapi() -> dict:
        if app.openapi_schema:
            return app.openapi_schema

        schema = get_openapi(
            title=app.title,
            version=app.version,
            description=app.description,
            routes=app.routes,
            tags=app.openapi_tags,
            servers=app.servers,
            contact=app.contact,
            license_info=app.license_info,
        )
        components = schema.setdefault("components", {})
        schemas = components.setdefault("schemas", {})

        err_schema = ErrorResponse.model_json_schema(ref_template="#/components/schemas/{model}")
        for def_name, def_body in err_schema.pop("$defs", {}).items():
            schemas.setdefault(def_name, def_body)
        schemas.setdefault("ErrorResponse", err_schema)

        security_schemes = components.setdefault("securitySchemes", {})
        security_schemes["BearerAuth"] = {
            "type": "http",
            "scheme": "bearer",
            "bearerFormat": "OpaqueSessionToken",
            "description": (
                "Token de sesion opaco emitido por POST /api/v1/auth/login "
                "(validado por digest SHA-256 en PostgreSQL user_session)."
            ),
        }
        security_schemes["SessionCookieAuth"] = {
            "type": "apiKey",
            "in": "cookie",
            "name": resolved_settings.session_cookie_name,
            "description": "Cookie HttpOnly de sesion administrativa (prohibido almacenar en localStorage).",
        }

        error_ref = {"$ref": "#/components/schemas/ErrorResponse"}
        protected_responses = {
            "401": {"description": "Autenticacion requerida o sesion expirada.", "content": {"application/json": {"schema": error_ref}}},
            "403": {"description": "Permisos RBAC insuficientes para el rol activo.", "content": {"application/json": {"schema": error_ref}}},
            "409": {"description": "Violacion de regla cientifica o conflicto optimista de version.", "content": {"application/json": {"schema": error_ref}}},
            "503": {"description": "Motor de persistencia temporalmente inaccesible.", "content": {"application/json": {"schema": error_ref}}},
        }

        admin_prefix = f"{resolved_settings.api_v1_prefix}/admin/"
        protected_auth_paths = {
            f"{resolved_settings.api_v1_prefix}/auth/me",
            f"{resolved_settings.api_v1_prefix}/auth/logout",
        }

        for path, path_item in schema.get("paths", {}).items():
            if not (path.startswith(admin_prefix) or path in protected_auth_paths):
                continue
            for method_name, operation in path_item.items():
                if method_name.lower() not in {"get", "post", "put", "patch", "delete"}:
                    continue
                operation["security"] = [{"BearerAuth": []}, {"SessionCookieAuth": []}]
                op_responses = operation.setdefault("responses", {})
                codes = ("401", "403", "409", "503") if path.startswith(admin_prefix) else ("401", "503")
                for code in codes:
                    op_responses.setdefault(code, protected_responses[code])

        app.openapi_schema = schema
        return app.openapi_schema

    app.openapi = custom_openapi  # type: ignore[method-assign]
    return app


def run() -> None:
    """Punto de entrada CLI para ejecutar el servidor HTTP con Uvicorn."""
    settings = get_settings()
    app = create_app(settings)
    uvicorn.run(
        app,
        host=settings.backend_host,
        port=settings.backend_port,
        log_level=settings.log_level.lower(),
    )
