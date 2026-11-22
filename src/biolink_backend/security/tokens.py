from __future__ import annotations

import hashlib
import secrets


def generate_session_token() -> str:
    """Genera un token opaco criptografico de 256 bits para cookie HttpOnly de sesion."""
    return secrets.token_urlsafe(32)


def hash_session_token(raw_token: str) -> str:
    """Calcula el digest SHA-256 para almacenamiento seguro en PostgreSQL (user_session.token_hash)."""
    cleaned = raw_token.strip()
    if not cleaned:
        raise ValueError("El token de sesion no puede estar vacio.")
    return hashlib.sha256(cleaned.encode("utf-8")).hexdigest()
