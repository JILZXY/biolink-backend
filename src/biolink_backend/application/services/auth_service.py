from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from biolink_backend.application.ports import AuthRepositoryPort
from biolink_backend.config.settings import Settings
from biolink_backend.security.passwords import verify_password
from biolink_backend.security.rbac import AuthenticatedUser
from biolink_backend.security.tokens import generate_session_token, hash_session_token
from biolink_backend.shared.exceptions import AuthenticationError
from biolink_backend.shared.ids import generate_domain_id
from biolink_backend.shared.time import utc_now


@dataclass(frozen=True, slots=True)
class SessionCreationResult:
    raw_token: str
    expires_at: datetime
    user: AuthenticatedUser


class AuthService:
    """Servicio de autenticacion de usuarios internos (V1 sin registro publico)."""

    def __init__(self, *, repository: AuthRepositoryPort, settings: Settings) -> None:
        self._repository = repository
        self._settings = settings

    def authenticate_internal_user(
        self,
        *,
        email: str,
        plain_password: str,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> SessionCreationResult:
        user_record = self._repository.get_user_by_email(email)
        if user_record is None or not verify_password(plain_password, user_record.password_hash):
            raise AuthenticationError("Credenciales invalidas.")
        if not user_record.is_active:
            raise AuthenticationError("La cuenta de usuario interno esta deshabilitada.")

        raw_token = generate_session_token()
        token_hash = hash_session_token(raw_token)
        session_id = generate_domain_id("session")
        expires_at = utc_now() + timedelta(hours=self._settings.session_ttl_hours)

        self._repository.create_session(
            session_id=session_id,
            user_id=user_record.user_id,
            token_hash=token_hash,
            expires_at=expires_at,
            ip_address=ip_address,
            user_agent=user_agent,
        )
        return SessionCreationResult(
            raw_token=raw_token,
            expires_at=expires_at,
            user=AuthenticatedUser(
                user_id=user_record.user_id,
                email=user_record.email,
                display_name=user_record.display_name,
                roles=user_record.roles,
                is_active=user_record.is_active,
            ),
        )

    def resolve_session(self, raw_token: str | None) -> AuthenticatedUser:
        if not raw_token or not raw_token.strip():
            raise AuthenticationError("No se proporciono token de sesion.")
        try:
            token_hash = hash_session_token(raw_token)
        except ValueError as exc:
            raise AuthenticationError("Token de sesion invalido.") from exc

        session = self._repository.get_active_session_by_token_hash(token_hash)
        if session is None or not session.is_active:
            raise AuthenticationError("Sesion invalida o expirada.")

        return AuthenticatedUser(
            user_id=session.user_id,
            email=session.email,
            display_name=session.display_name,
            roles=session.roles,
            is_active=session.is_active,
        )

    def logout(self, raw_token: str | None) -> bool:
        if not raw_token or not raw_token.strip():
            return False
        token_hash = hash_session_token(raw_token)
        return self._repository.revoke_session_by_token_hash(token_hash)
