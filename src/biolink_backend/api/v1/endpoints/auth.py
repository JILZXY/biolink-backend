from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response

from biolink_backend.api.dependencies import (
    extract_session_token,
    get_app_settings,
    get_auth_service,
    get_current_user,
)
from biolink_backend.api.v1.schemas.admin_curation import (
    AuthenticatedUserSchema,
    AuthSessionResponseSchema,
    LoginRequestSchema,
    LogoutResponseSchema,
)
from biolink_backend.application.services.auth_service import AuthService
from biolink_backend.config.settings import Settings
from biolink_backend.security.rbac import AuthenticatedUser

router = APIRouter(prefix="/auth", tags=["Authentication"])


@router.post(
    "/login",
    response_model=AuthSessionResponseSchema,
    summary="Autenticacion de usuario interno (Curador / Revisor / Admin)",
    description="Autentica credenciales contra app_user en PostgreSQL y emite sesion segura HttpOnly + token opaco.",
)
def login_internal_user(
    payload: LoginRequestSchema,
    request: Request,
    response: Response,
    settings: Annotated[Settings, Depends(get_app_settings)],
    auth_service: Annotated[AuthService, Depends(get_auth_service)],
) -> AuthSessionResponseSchema:
    client_ip = request.client.host if request.client else None
    user_agent = request.headers.get("User-Agent")
    session = auth_service.authenticate_internal_user(
        email=payload.email,
        plain_password=payload.password,
        ip_address=client_ip,
        user_agent=user_agent,
    )
    response.set_cookie(
        key=settings.session_cookie_name,
        value=session.raw_token,
        max_age=settings.session_ttl_hours * 3600,
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite="lax",
    )
    return AuthSessionResponseSchema(
        access_token=session.raw_token,
        expires_at=session.expires_at,
        user=AuthenticatedUserSchema(
            user_id=session.user.user_id,
            email=session.user.email,
            display_name=session.user.display_name,
            roles=sorted(role.value for role in session.user.roles),
            is_active=session.user.is_active,
        ),
    )


@router.post(
    "/logout",
    response_model=LogoutResponseSchema,
    summary="Cerrar sesion administrativa",
    description="Revoca inmediatamente el token activo en user_session y limpia la cookie HttpOnly.",
)
def logout_internal_user(
    request: Request,
    response: Response,
    settings: Annotated[Settings, Depends(get_app_settings)],
    auth_service: Annotated[AuthService, Depends(get_auth_service)],
) -> LogoutResponseSchema:
    raw_token = extract_session_token(request, settings)
    revoked = auth_service.logout(raw_token)
    response.delete_cookie(key=settings.session_cookie_name)
    return LogoutResponseSchema(
        revoked=revoked,
        message="Sesion revocada exitosamente." if revoked else "No existia sesion activa para revocar.",
    )


@router.get(
    "/me",
    response_model=AuthenticatedUserSchema,
    summary="Consultar identidad y roles de la sesion actual",
)
def get_authenticated_profile(
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
) -> AuthenticatedUserSchema:
    return AuthenticatedUserSchema(
        user_id=current_user.user_id,
        email=current_user.email,
        display_name=current_user.display_name,
        roles=sorted(role.value for role in current_user.roles),
        is_active=current_user.is_active,
    )
