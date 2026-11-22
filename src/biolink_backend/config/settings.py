from __future__ import annotations

import json
from functools import lru_cache
from typing import Annotated, Any, Literal
from urllib.parse import quote_plus

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


def _reject_latest_tag(image: str, field_name: str) -> str:
    cleaned = image.strip()
    if not cleaned:
        raise ValueError(f"{field_name} no puede estar vacio.")
    if cleaned.endswith(":latest") or ":latest@" in cleaned:
        raise ValueError(f"{field_name} tiene prohibido usar el tag ':latest': '{cleaned}'.")
    if ":" not in cleaned and "@" not in cleaned:
        raise ValueError(f"{field_name} debe especificar una version o digest explicito: '{cleaned}'.")
    return cleaned


class Settings(BaseSettings):
    """Configuracion centralizada y validada para biolink-backend."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Aplicacion y API REST ---
    app_name: str = Field(default="biolink-backend", min_length=1)
    app_env: Literal["development", "test", "staging", "production"] = "development"
    app_version: str = Field(default="0.1.0", min_length=1)
    api_v1_prefix: str = Field(default="/api/v1")
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    log_format: Literal["json", "text"] = "json"

    # --- Red y CORS restringido ---
    backend_host: str = Field(default="127.0.0.1", min_length=1)
    backend_port: int = Field(default=8000, ge=1, le=65535)
    cors_allowed_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://127.0.0.1:3000", "http://localhost:3000"]
    )

    # --- Seguridad y Sesiones ---
    auth_secret_key: SecretStr = Field(
        default=SecretStr("DEV_ONLY_REPLACE_THIS_SECRET_MIN_32_CHARACTERS")
    )
    session_cookie_name: str = Field(default="biolink_session", min_length=3)
    session_ttl_hours: int = Field(default=12, ge=1, le=168)
    session_cookie_secure: bool = False

    # --- Presupuesto de Memoria del Backend + Worker ---
    backend_mem_limit_mb: int = Field(default=320, ge=64, le=512)
    worker_mem_limit_mb: int = Field(default=192, ge=64, le=512)

    # --- PostgreSQL 18.7 ---
    postgres_image: str = "postgres:18.7"
    postgres_host: str = Field(default="127.0.0.1", min_length=1)
    postgres_port: int = Field(default=5432, ge=1, le=65535)
    postgres_db: str = Field(default="biolink", min_length=1)
    postgres_user: str = Field(default="biolink_app", min_length=1)
    postgres_password: SecretStr = Field(default=SecretStr("CHANGE_ME_PG_SECRET"))
    postgres_pool_min_size: int = Field(default=2, ge=1, le=50)
    postgres_pool_max_size: int = Field(default=10, ge=1, le=100)
    postgres_connect_timeout_sec: float = Field(default=5.0, gt=0.0, le=60.0)
    postgres_statement_timeout_ms: int = Field(default=10000, ge=500, le=120000)
    postgres_mem_limit_mb: int = Field(default=768, ge=128, le=768)

    # --- MongoDB Community 8.0.38 ---
    mongodb_image: str = "mongo:8.0.38"
    mongodb_host: str = Field(default="127.0.0.1", min_length=1)
    mongodb_port: int = Field(default=27017, ge=1, le=65535)
    mongodb_db: str = Field(default="biolink_documents", min_length=1)
    mongodb_user: str = Field(default="biolink_app", min_length=1)
    mongodb_password: SecretStr = Field(default=SecretStr("CHANGE_ME_MONGO_SECRET"))
    mongodb_auth_source: str = Field(default="admin", min_length=1)
    mongodb_min_pool_size: int = Field(default=1, ge=0, le=50)
    mongodb_max_pool_size: int = Field(default=10, ge=1, le=100)
    mongodb_connect_timeout_ms: int = Field(default=5000, ge=500, le=60000)
    mongodb_server_selection_timeout_ms: int = Field(default=5000, ge=500, le=60000)
    mongodb_socket_timeout_ms: int = Field(default=10000, ge=500, le=120000)
    mongodb_mem_limit_mb: int = Field(default=768, ge=128, le=768)

    # --- Neo4j Community 2026.10.2 ---
    neo4j_image: str = "neo4j:2026.10.2-community"
    neo4j_host: str = Field(default="127.0.0.1", min_length=1)
    neo4j_bolt_port: int = Field(default=7687, ge=1, le=65535)
    neo4j_http_port: int = Field(default=7474, ge=1, le=65535)
    neo4j_user: str = Field(default="neo4j", min_length=1)
    neo4j_password: SecretStr = Field(default=SecretStr("CHANGE_ME_NEO4J_SECRET"))
    neo4j_max_connection_pool_size: int = Field(default=15, ge=1, le=100)
    neo4j_connection_timeout_sec: float = Field(default=5.0, gt=0.0, le=60.0)
    neo4j_max_transaction_retry_time_sec: float = Field(default=15.0, gt=0.0, le=120.0)
    neo4j_mem_limit_mb: int = Field(default=1024, ge=256, le=1024)
    neo4j_heap_initial: str = "256m"
    neo4j_heap_max: str = "384m"
    neo4j_pagecache: str = "256m"
    neo4j_tx_total_max: str = "256m"
    neo4j_tx_max: str = "64m"

    # --- Transactional Outbox Worker ---
    outbox_poll_interval_sec: float = Field(default=2.0, gt=0.1, le=300.0)
    outbox_batch_size: int = Field(default=50, ge=1, le=500)
    outbox_max_attempts: int = Field(default=5, ge=1, le=50)

    @field_validator("api_v1_prefix")
    @classmethod
    def _validate_api_prefix(cls, value: str) -> str:
        if not value.startswith("/"):
            raise ValueError("api_v1_prefix debe iniciar con '/'.")
        return value.rstrip("/")

    @field_validator("cors_allowed_origins", mode="before")
    @classmethod
    def _parse_cors_origins(cls, value: Any) -> list[str]:
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return []
            if stripped.startswith("["):
                parsed = json.loads(stripped)
                if not isinstance(parsed, list):
                    raise ValueError("CORS_ALLOWED_ORIGINS JSON debe ser una lista.")
                return [str(item).strip() for item in parsed if str(item).strip()]
            return [item.strip() for item in stripped.split(",") if item.strip()]
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        raise ValueError("CORS_ALLOWED_ORIGINS debe ser una lista o cadena separada por comas.")

    @field_validator("cors_allowed_origins")
    @classmethod
    def _validate_cors_restricted(cls, origins: list[str]) -> list[str]:
        if not origins:
            raise ValueError("CORS_ALLOWED_ORIGINS debe especificar al menos un origen valido.")
        for origin in origins:
            if "*" in origin:
                raise ValueError("CORS restringido: esta prohibido usar comodin '*' en origenes.")
            if not (origin.startswith("http://") or origin.startswith("https://")):
                raise ValueError(f"Origen CORS invalido (debe iniciar con http:// o https://): '{origin}'.")
        return origins

    @field_validator("postgres_image")
    @classmethod
    def _check_pg_image(cls, value: str) -> str:
        return _reject_latest_tag(value, "postgres_image")

    @field_validator("mongodb_image")
    @classmethod
    def _check_mongo_image(cls, value: str) -> str:
        return _reject_latest_tag(value, "mongodb_image")

    @field_validator("neo4j_image")
    @classmethod
    def _check_neo4j_image(cls, value: str) -> str:
        return _reject_latest_tag(value, "neo4j_image")

    @field_validator("auth_secret_key")
    @classmethod
    def _check_auth_secret(cls, value: SecretStr) -> SecretStr:
        raw = value.get_secret_value().strip()
        if len(raw) < 32:
            raise ValueError("AUTH_SECRET_KEY debe tener al menos 32 caracteres.")
        return value

    @field_validator("postgres_password", "mongodb_password", "neo4j_password")
    @classmethod
    def _check_nonempty_password(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().strip():
            raise ValueError("Las credenciales de bases de datos no pueden estar vacias.")
        return value

    @model_validator(mode="after")
    def _validate_cross_fields(self) -> Settings:
        if self.postgres_pool_max_size < self.postgres_pool_min_size:
            raise ValueError("POSTGRES_POOL_MAX_SIZE debe ser mayor o igual a POSTGRES_POOL_MIN_SIZE.")
        if self.mongodb_max_pool_size < self.mongodb_min_pool_size:
            raise ValueError("MONGODB_MAX_POOL_SIZE debe ser mayor o igual a MONGODB_MIN_POOL_SIZE.")

        backend_total = self.backend_mem_limit_mb + self.worker_mem_limit_mb
        if backend_total > 512:
            raise ValueError(
                f"Presupuesto combinado de backend + worker ({backend_total} MiB) excede el maximo permitido de 512 MiB."
            )

        total_containers = (
            self.postgres_mem_limit_mb
            + self.mongodb_mem_limit_mb
            + self.neo4j_mem_limit_mb
            + backend_total
        )
        if total_containers > 3072:
            raise ValueError(
                f"Suma de limites de contenedores ({total_containers} MiB) supera 3,072 MiB; "
                "debe preservarse al menos 1,024 MiB libres dentro del tope global de 4,096 MiB."
            )

        if self.app_env == "production":
            insecure_markers = ("CHANGE_ME", "REPLACE_WITH", "DEV_ONLY")
            for field_name in ("auth_secret_key", "postgres_password", "mongodb_password", "neo4j_password"):
                secret_val = getattr(self, field_name).get_secret_value()
                if any(marker in secret_val for marker in insecure_markers):
                    raise ValueError(
                        f"El secreto '{field_name}' conserva un marcador de ejemplo en modo production."
                    )
            if not self.session_cookie_secure:
                raise ValueError("SESSION_COOKIE_SECURE debe ser true en modo production.")

        return self

    @property
    def postgres_dsn(self) -> str:
        user = quote_plus(self.postgres_user)
        pwd = quote_plus(self.postgres_password.get_secret_value())
        return f"postgresql://{user}:{pwd}@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"

    @property
    def postgres_masked_dsn(self) -> str:
        return f"postgresql://{self.postgres_user}:***@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"

    @property
    def mongodb_uri(self) -> str:
        user = quote_plus(self.mongodb_user)
        pwd = quote_plus(self.mongodb_password.get_secret_value())
        auth_src = quote_plus(self.mongodb_auth_source)
        return (
            f"mongodb://{user}:{pwd}@{self.mongodb_host}:{self.mongodb_port}/"
            f"{self.mongodb_db}?authSource={auth_src}"
        )

    @property
    def mongodb_masked_uri(self) -> str:
        return (
            f"mongodb://{self.mongodb_user}:***@{self.mongodb_host}:{self.mongodb_port}/"
            f"{self.mongodb_db}?authSource={self.mongodb_auth_source}"
        )

    @property
    def neo4j_uri(self) -> str:
        return f"bolt://{self.neo4j_host}:{self.neo4j_bolt_port}"

    def memory_budget_summary(self) -> dict[str, int]:
        backend_total = self.backend_mem_limit_mb + self.worker_mem_limit_mb
        containers_total = (
            self.postgres_mem_limit_mb
            + self.mongodb_mem_limit_mb
            + self.neo4j_mem_limit_mb
            + backend_total
        )
        return {
            "postgresql_mb": self.postgres_mem_limit_mb,
            "mongodb_mb": self.mongodb_mem_limit_mb,
            "neo4j_mb": self.neo4j_mem_limit_mb,
            "backend_api_mb": self.backend_mem_limit_mb,
            "worker_mb": self.worker_mem_limit_mb,
            "containers_total_mb": containers_total,
            "reserved_headroom_mb": 4096 - containers_total,
            "global_ceiling_mb": 4096,
        }


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Devuelve la instancia cacheada de configuracion."""
    return Settings()
