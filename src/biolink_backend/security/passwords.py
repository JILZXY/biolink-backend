from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

_ALGORITHM = "pbkdf2-sha256"
_DEFAULT_ITERATIONS = 600_000
_SALT_BYTES = 16
_DK_LEN = 32


def hash_password(plain_password: str, *, iterations: int = _DEFAULT_ITERATIONS) -> str:
    """Genera un hash PBKDF2-HMAC-SHA256 salado. Nunca almacena texto plano."""
    if not plain_password or len(plain_password) < 8:
        raise ValueError("La contrasena debe tener al menos 8 caracteres.")
    salt = secrets.token_bytes(_SALT_BYTES)
    digest = hashlib.pbkdf2_hmac("sha256", plain_password.encode("utf-8"), salt, iterations, dklen=_DK_LEN)
    salt_b64 = base64.b64encode(salt).decode("ascii")
    digest_b64 = base64.b64encode(digest).decode("ascii")
    return f"${_ALGORITHM}${iterations}${salt_b64}${digest_b64}"


def verify_password(plain_password: str, stored_hash: str) -> bool:
    """Verifica contrasena en tiempo constante mediante hmac.compare_digest."""
    if not plain_password or not stored_hash:
        return False
    try:
        parts = stored_hash.split("$")
        # Formato: ['', 'pbkdf2-sha256', '600000', '<salt_b64>', '<digest_b64>']
        if len(parts) != 5 or parts[1] != _ALGORITHM:
            return False
        iterations = int(parts[2])
        salt = base64.b64decode(parts[3].encode("ascii"))
        expected_digest = base64.b64decode(parts[4].encode("ascii"))
    except (ValueError, TypeError):
        return False

    computed_digest = hashlib.pbkdf2_hmac(
        "sha256",
        plain_password.encode("utf-8"),
        salt,
        iterations,
        dklen=len(expected_digest),
    )
    return hmac.compare_digest(computed_digest, expected_digest)
