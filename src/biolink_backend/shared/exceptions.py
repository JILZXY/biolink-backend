from __future__ import annotations

from typing import Any


class BioLinkError(Exception):
    """Excepcion base controlada de BioLink con mapeo directo a respuesta HTTP."""

    def __init__(
        self,
        *,
        code: str,
        message: str,
        http_status: int = 500,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status
        self.details = details or {}


class DomainValidationError(BioLinkError):
    """Error de validacion estructural o semantica en entidades y objetos de valor."""

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(
            code="DOMAIN_VALIDATION_ERROR",
            message=message,
            http_status=422,
            details=details,
        )


class BusinessRuleViolationError(BioLinkError):
    """Violacion de una regla cientifica o de dominio (RD-01..RD-16)."""

    def __init__(
        self,
        rule_id: str,
        message: str,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        merged = {"rule_id": rule_id, **(details or {})}
        super().__init__(
            code=f"RULE_VIOLATION_{rule_id.replace('-', '_')}",
            message=message,
            http_status=409,
            details=merged,
        )


class EntityNotFoundError(BioLinkError):
    """Entidad solicitada inexistente en la fuente de verdad."""

    def __init__(self, entity: str, entity_id: str) -> None:
        super().__init__(
            code="ENTITY_NOT_FOUND",
            message=f"No se encontro {entity} con identificador '{entity_id}'.",
            http_status=404,
            details={"entity": entity, "id": entity_id},
        )


class AuthenticationError(BioLinkError):
    """Fallo de autenticacion o sesion expirada/invalida."""

    def __init__(self, message: str = "Autenticacion requerida o credenciales invalidas.") -> None:
        super().__init__(
            code="AUTHENTICATION_REQUIRED",
            message=message,
            http_status=401,
        )


class AuthorizationError(BioLinkError):
    """Usuario autenticado sin permisos suficientes para la operacion."""

    def __init__(self, message: str = "No cuenta con privilegios suficientes para esta accion.") -> None:
        super().__init__(
            code="AUTHORIZATION_DENIED",
            message=message,
            http_status=403,
        )


class DatabaseUnavailableError(BioLinkError):
    """Motor de persistencia temporalmente inaccesible."""

    def __init__(self, engine: str, detail: str | None = None) -> None:
        super().__init__(
            code="DATABASE_UNAVAILABLE",
            message=f"El motor '{engine}' no esta disponible en este momento.",
            http_status=503,
            details={"engine": engine, **({"detail": detail} if detail else {})},
        )
