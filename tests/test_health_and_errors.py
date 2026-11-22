from __future__ import annotations

from fastapi.testclient import TestClient

from biolink_backend.config.settings import Settings
from biolink_backend.main import create_app
from biolink_backend.shared.exceptions import (
    BusinessRuleViolationError,
    DatabaseUnavailableError,
    DomainValidationError,
    EntityNotFoundError,
)
from tests.conftest import build_fake_health_service


def test_liveness_always_returns_200_even_when_all_databases_are_down(test_settings: Settings) -> None:
    svc = build_fake_health_service(test_settings, pg_ok=False, mongo_ok=False, neo4j_ok=False)
    app = create_app(test_settings, health_service=svc)

    with TestClient(app) as client:
        resp = client.get("/api/v1/health/live")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "alive"
        assert body["service"] == "biolink-backend"
        assert body["environment"] == "test"


def test_readiness_returns_200_when_all_three_engines_healthy(test_settings: Settings) -> None:
    svc = build_fake_health_service(test_settings, pg_ok=True, mongo_ok=True, neo4j_ok=True)
    app = create_app(test_settings, health_service=svc)

    with TestClient(app) as client:
        resp = client.get("/api/v1/health/ready")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ready"
        assert body["ready"] is True
        assert body["dependencies"]["postgresql"]["healthy"] is True
        assert body["dependencies"]["mongodb"]["healthy"] is True
        assert body["dependencies"]["neo4j"]["healthy"] is True

        cons = client.get("/api/v1/health")
        assert cons.status_code == 200
        cons_body = cons.json()
        assert cons_body["status"] == "ok"
        assert cons_body["live"] is True
        assert cons_body["ready"] is True
        assert cons_body["memory_budget_mb"]["containers_total_mb"] == 3072


def test_readiness_returns_503_when_any_engine_is_unavailable(test_settings: Settings) -> None:
    for flags in (
        {"pg_ok": False, "mongo_ok": True, "neo4j_ok": True},
        {"pg_ok": True, "mongo_ok": False, "neo4j_ok": True},
        {"pg_ok": True, "mongo_ok": True, "neo4j_ok": False},
    ):
        svc = build_fake_health_service(test_settings, **flags)
        app = create_app(test_settings, health_service=svc)
        with TestClient(app) as client:
            resp = client.get("/api/v1/health/ready")
            assert resp.status_code == 503
            body = resp.json()
            assert body["status"] == "not_ready"
            assert body["ready"] is False

            cons = client.get("/api/v1/health")
            assert cons.status_code == 503
            assert cons.json()["status"] == "degraded"
            assert cons.json()["live"] is True


def test_uniform_error_handlers(test_settings: Settings) -> None:
    svc = build_fake_health_service(test_settings)
    app = create_app(test_settings, health_service=svc)

    @app.get("/api/v1/_test/err/domain")
    def _domain_err() -> None:
        raise DomainValidationError("Campo invalido")

    @app.get("/api/v1/_test/err/rule")
    def _rule_err() -> None:
        raise BusinessRuleViolationError("RD-01", "Ocurrencia no es interaccion")

    @app.get("/api/v1/_test/err/notfound")
    def _not_found() -> None:
        raise EntityNotFoundError("Taxon", "TX_999")

    @app.get("/api/v1/_test/err/db")
    def _db_err() -> None:
        raise DatabaseUnavailableError("neo4j", "Bolt timeout")

    @app.get("/api/v1/_test/err/unexpected")
    def _unexpected() -> None:
        raise RuntimeError("secret stack trace info")

    with TestClient(app, raise_server_exceptions=False) as client:
        r1 = client.get("/api/v1/_test/err/domain")
        assert r1.status_code == 422
        assert r1.json()["error"]["code"] == "DOMAIN_VALIDATION_ERROR"
        assert r1.json()["error"]["request_id"] is not None

        r2 = client.get("/api/v1/_test/err/rule")
        assert r2.status_code == 409
        assert r2.json()["error"]["code"] == "RULE_VIOLATION_RD_01"

        r3 = client.get("/api/v1/_test/err/notfound")
        assert r3.status_code == 404
        assert r3.json()["error"]["code"] == "ENTITY_NOT_FOUND"

        r4 = client.get("/api/v1/_test/err/db")
        assert r4.status_code == 503
        assert r4.json()["error"]["code"] == "DATABASE_UNAVAILABLE"

        r5 = client.get("/api/v1/_test/err/unexpected")
        assert r5.status_code == 500
        assert r5.json()["error"]["code"] == "INTERNAL_SERVER_ERROR"
        assert "secret stack trace info" not in r5.text
