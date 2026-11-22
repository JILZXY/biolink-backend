from __future__ import annotations

from fastapi.testclient import TestClient

from biolink_backend.config.settings import Settings
from biolink_backend.main import create_app
from tests.conftest import build_fake_health_service


def test_fastapi_startup_and_openapi_routes(test_settings: Settings) -> None:
    health_svc = build_fake_health_service(test_settings)
    app = create_app(test_settings, health_service=health_svc)

    with TestClient(app) as client:
        openapi_resp = client.get("/api/v1/openapi.json")
        assert openapi_resp.status_code == 200
        schema = openapi_resp.json()
        assert schema["info"]["title"] == "BioLink API"
        paths = schema["paths"]
        assert "/api/v1/health" in paths
        assert "/api/v1/health/live" in paths
        assert "/api/v1/health/ready" in paths

        sec_schemes = schema["components"]["securitySchemes"]
        assert "BearerAuth" in sec_schemes
        assert "SessionCookieAuth" in sec_schemes
        assert "ErrorResponse" in schema["components"]["schemas"]
        assert paths["/api/v1/admin/assertions/{assertion_id}/review"]["post"]["security"] == [
            {"BearerAuth": []},
            {"SessionCookieAuth": []},
        ]
        assert "401" in paths["/api/v1/admin/assertions/{assertion_id}/review"]["post"]["responses"]
        assert "403" in paths["/api/v1/admin/assertions/{assertion_id}/review"]["post"]["responses"]


def test_security_headers_and_request_id_propagation(test_settings: Settings) -> None:
    health_svc = build_fake_health_service(test_settings)
    app = create_app(test_settings, health_service=health_svc)

    with TestClient(app) as client:
        resp = client.get("/api/v1/health/live", headers={"X-Request-ID": "req-biolink-123"})
        assert resp.status_code == 200
        assert resp.headers["X-Request-ID"] == "req-biolink-123"
        assert resp.headers["X-Content-Type-Options"] == "nosniff"
        assert resp.headers["X-Frame-Options"] == "DENY"
        assert resp.headers["Cache-Control"] == "no-store"

        auto_resp = client.get("/api/v1/health/live")
        assert auto_resp.status_code == 200
        assert len(auto_resp.headers["X-Request-ID"]) > 10


def test_restricted_cors_policy(test_settings: Settings) -> None:
    health_svc = build_fake_health_service(test_settings)
    app = create_app(test_settings, health_service=health_svc)

    with TestClient(app) as client:
        allowed = client.get(
            "/api/v1/health/live",
            headers={"Origin": "http://127.0.0.1:3000"},
        )
        assert allowed.headers.get("access-control-allow-origin") == "http://127.0.0.1:3000"

        blocked = client.get(
            "/api/v1/health/live",
            headers={"Origin": "https://evil-domain.example.com"},
        )
        assert "access-control-allow-origin" not in blocked.headers
