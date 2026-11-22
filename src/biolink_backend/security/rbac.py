from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from biolink_backend.domain.vocabularies import RoleCode
from biolink_backend.shared.exceptions import AuthenticationError, AuthorizationError


@dataclass(frozen=True, slots=True)
class AuthenticatedUser:
    """Representacion del actor interno autenticado (Curador, Revisor o Administrador)."""

    user_id: str
    email: str
    display_name: str
    roles: frozenset[RoleCode]
    is_active: bool

    def has_any_role(self, allowed: Iterable[RoleCode]) -> bool:
        return bool(self.roles.intersection(allowed))


def ensure_authenticated(user: AuthenticatedUser | None) -> AuthenticatedUser:
    """Valida que exista una sesion activa y usuario interno habilitado."""
    if user is None:
        raise AuthenticationError("Se requiere sesion interna valida para acceder a este recurso.")
    if not user.is_active:
        raise AuthenticationError("La cuenta administrativa se encuentra inactiva.")
    return user


def ensure_has_any_role(
    user: AuthenticatedUser | None,
    allowed_roles: Iterable[RoleCode],
) -> AuthenticatedUser:
    """Aplica control de acceso basado en roles (RBAC) para operaciones administrativas."""
    active_user = ensure_authenticated(user)
    required = frozenset(allowed_roles)
    if not active_user.has_any_role(required):
        raise AuthorizationError(
            f"Permiso denegado: se requiere alguno de los roles {[r.value for r in sorted(required)]}."
        )
    return active_user
