from __future__ import annotations

from dataclasses import dataclass

import pytest
from pydantic import SecretStr

from biolink_backend.application.ports import EngineHealthStatus
from biolink_backend.application.services.health_service import HealthService
from biolink_backend.config.settings import Settings


@dataclass
class FakeHealthPort:
    engine: str
    healthy: bool = True
    version: str = "test-1.0"
    error: str | None = None

    def check_health(self) -> EngineHealthStatus:
        return EngineHealthStatus(
            engine=self.engine,
            healthy=self.healthy,
            latency_ms=1.25,
            version=self.version if self.healthy else None,
            error=self.error if not self.healthy else None,
        )


@pytest.fixture
def test_settings() -> Settings:
    return Settings(
        _env_file=None,
        app_env="test",
        auth_secret_key=SecretStr("TEST_SECRET_KEY_FOR_PYTEST_ONLY_MIN_32_CHARS"),
        postgres_password=SecretStr("test_pg_pass"),
        mongodb_password=SecretStr("test_mongo_pass"),
        neo4j_password=SecretStr("test_neo4j_pass"),
        cors_allowed_origins=["http://127.0.0.1:3000"],
    )


def build_fake_health_service(
    settings: Settings,
    *,
    pg_ok: bool = True,
    mongo_ok: bool = True,
    neo4j_ok: bool = True,
) -> HealthService:
    return HealthService(
        settings=settings,
        relational=FakeHealthPort("postgresql", healthy=pg_ok, version="18.7", error="pg down"),
        document=FakeHealthPort("mongodb", healthy=mongo_ok, version="8.0.38", error="mongo down"),
        graph=FakeHealthPort("neo4j", healthy=neo4j_ok, version="2026.10.2", error="neo4j down"),
        started_monotonic=0.0,
    )
