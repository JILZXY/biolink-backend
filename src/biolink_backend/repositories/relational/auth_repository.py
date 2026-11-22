from __future__ import annotations

import ipaddress
import json
from datetime import datetime

from biolink_backend.application.ports import StoredSessionRecord, StoredUserRecord
from biolink_backend.domain.vocabularies import RoleCode
from biolink_backend.infrastructure.postgresql import PostgresConnectionProvider


def _normalize_inet_or_none(raw_ip: str | None) -> str | None:
    if not raw_ip:
        return None
    try:
        return str(ipaddress.ip_address(raw_ip.strip()))
    except ValueError:
        return None


def _parse_roles(raw_roles: list[str] | None) -> frozenset[RoleCode]:
    if not raw_roles:
        return frozenset()
    parsed: set[RoleCode] = set()
    for code in raw_roles:
        if code:
            parsed.add(RoleCode(code))
    return frozenset(parsed)


class PostgresAuthRepository:
    """Repositorio SQL nativo (sin ORM) para usuarios internos, roles y sesiones."""

    def __init__(self, provider: PostgresConnectionProvider) -> None:
        self._provider = provider

    def get_user_by_email(self, email: str) -> StoredUserRecord | None:
        sql = """
        SELECT
            u.user_id,
            u.display_name,
            u.email,
            u.password_hash,
            u.is_active,
            COALESCE(
                array_agg(r.code) FILTER (WHERE r.code IS NOT NULL),
                ARRAY[]::varchar[]
            ) AS role_codes
        FROM app_user u
        LEFT JOIN user_role ur ON ur.user_id = u.user_id
        LEFT JOIN role r ON r.role_id = ur.role_id
        WHERE lower(u.email) = lower(%s)
        GROUP BY u.user_id;
        """
        with self._provider.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (email.strip(),))
                row = cur.fetchone()
        if not row:
            return None
        return StoredUserRecord(
            user_id=row["user_id"],
            display_name=row["display_name"],
            email=row["email"],
            password_hash=row["password_hash"],
            is_active=bool(row["is_active"]),
            roles=_parse_roles(row["role_codes"]),
        )

    def create_session(
        self,
        *,
        session_id: str,
        user_id: str,
        token_hash: str,
        expires_at: datetime,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> None:
        sql = """
        INSERT INTO user_session (
            session_id, user_id, token_hash, ip_address, user_agent, expires_at
        ) VALUES (%s, %s, %s, %s::inet, %s, %s);
        """
        audit_sql = """
        INSERT INTO audit_log (entity_type, entity_id, action, performed_by, details)
        VALUES ('session', %s, 'LOGIN', %s, %s::jsonb);
        """
        safe_ip = _normalize_inet_or_none(ip_address)
        details_json = json.dumps({"ip_address": ip_address, "user_agent": user_agent})
        with self._provider.connection() as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(
                        sql,
                        (session_id, user_id, token_hash, safe_ip, user_agent, expires_at),
                    )
                    cur.execute(audit_sql, (session_id, user_id, details_json))

    def get_active_session_by_token_hash(self, token_hash: str) -> StoredSessionRecord | None:
        sql = """
        SELECT
            s.session_id,
            s.expires_at,
            u.user_id,
            u.email,
            u.display_name,
            u.is_active,
            COALESCE(
                array_agg(r.code) FILTER (WHERE r.code IS NOT NULL),
                ARRAY[]::varchar[]
            ) AS role_codes
        FROM user_session s
        JOIN app_user u ON u.user_id = s.user_id
        LEFT JOIN user_role ur ON ur.user_id = u.user_id
        LEFT JOIN role r ON r.role_id = ur.role_id
        WHERE s.token_hash = %s
          AND s.revoked_at IS NULL
          AND s.expires_at > clock_timestamp()
        GROUP BY s.session_id, s.expires_at, u.user_id;
        """
        with self._provider.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (token_hash,))
                row = cur.fetchone()
        if not row:
            return None
        return StoredSessionRecord(
            session_id=row["session_id"],
            user_id=row["user_id"],
            email=row["email"],
            display_name=row["display_name"],
            is_active=bool(row["is_active"]),
            roles=_parse_roles(row["role_codes"]),
            expires_at=row["expires_at"],
        )

    def revoke_session_by_token_hash(self, token_hash: str) -> bool:
        sql = """
        UPDATE user_session
        SET revoked_at = clock_timestamp()
        WHERE token_hash = %s
          AND revoked_at IS NULL
        RETURNING session_id, user_id;
        """
        audit_sql = """
        INSERT INTO audit_log (entity_type, entity_id, action, performed_by, details)
        VALUES ('session', %s, 'LOGOUT', %s, '{}'::jsonb);
        """
        with self._provider.connection() as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(sql, (token_hash,))
                    row = cur.fetchone()
                    if row is None:
                        return False
                    cur.execute(audit_sql, (row["session_id"], row["user_id"]))
                    return True
