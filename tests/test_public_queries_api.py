from __future__ import annotations

from fastapi.testclient import TestClient

from biolink_backend.api.dependencies import get_public_query_service
from biolink_backend.application.services.public_query_service import PublicQueryService
from biolink_backend.config.settings import Settings
from biolink_backend.main import create_app
from biolink_backend.shared.exceptions import DatabaseUnavailableError
from tests.conftest import build_fake_health_service
from tests.test_public_queries_service import (
    FakeDocumentPort,
    FakeGraphPort,
    FakeRelationalPort,
    _sample_fixtures,
)


def _build_client(test_settings: Settings, *, fail_mongo: bool = False) -> TestClient:
    fx = _sample_fixtures()
    doc_port = FakeDocumentPort(
        fail_with=DatabaseUnavailableError("mongodb", "cluster unreachable") if fail_mongo else None
    )
    svc = PublicQueryService(
        relational=FakeRelationalPort(fx),
        document=doc_port,
        graph=FakeGraphPort(),
    )
    app = create_app(test_settings, health_service=build_fake_health_service(test_settings))
    app.dependency_overrides[get_public_query_service] = lambda: svc
    return TestClient(app)


def test_openapi_documents_all_nine_public_query_routes(test_settings: Settings) -> None:
    with _build_client(test_settings) as client:
        paths = client.get("/api/v1/openapi.json").json()["paths"]
        expected = {
            "/api/v1/taxa",
            "/api/v1/taxa/{taxon_id}",
            "/api/v1/taxa/{taxon_id}/interactions",
            "/api/v1/taxa/{taxon_id}/connectivity",
            "/api/v1/interactions/{assertion_id}",
            "/api/v1/network/subgraph",
            "/api/v1/network/compare-localities",
            "/api/v1/sources/{source_id}",
            "/api/v1/occurrences",
        }
        assert expected.issubset(set(paths.keys()))


def test_public_endpoints_happy_paths_and_status_codes(test_settings: Settings) -> None:
    with _build_client(test_settings) as client:
        # OP-01
        r1 = client.get("/api/v1/taxa", params={"q": "Sturnira parvidens"})
        assert r1.status_code == 200
        assert r1.json()["items"][0]["taxon_id"] == "TAX_AN_003"

        # OP-02
        r2 = client.get("/api/v1/taxa/TAX_AN_001")
        assert r2.status_code == 200
        assert r2.json()["has_dossier"] is True

        # OP-03
        r3 = client.get("/api/v1/taxa/TAX_AN_001/interactions")
        assert r3.status_code == 200
        assert len(r3.json()["items"]) == 2

        # OP-04
        r4 = client.get("/api/v1/interactions/AST_SCI_001")
        assert r4.status_code == 200
        assert r4.json()["graph_projected"] is True

        # OP-05
        r5 = client.get("/api/v1/network/subgraph", params={"locality_id": "LOC_RBMA_SOUTH"})
        assert r5.status_code == 200
        assert len(r5.json()["interactions"]) == 1

        # OP-06
        r6 = client.get(
            "/api/v1/network/compare-localities",
            params={"locality_id_a": "LOC_RBMA_SOUTH", "locality_id_b": "LOC_PDLG"},
        )
        assert r6.status_code == 200
        assert r6.json()["undetermined_locality_assertions_count"] == 1

        # OP-07
        r7 = client.get("/api/v1/taxa/TAX_AN_001/connectivity")
        assert r7.status_code == 200
        assert r7.json()["distinct_partner_taxa_count"] == 1
        assert r7.json()["documented_assertions_count"] == 2

        # OP-08
        r8 = client.get("/api/v1/sources/BAT2007")
        assert r8.status_code == 200
        assert r8.json()["has_study_dossier"] is True

        # OP-15
        r15 = client.get("/api/v1/occurrences")
        assert r15.status_code == 200
        assert r15.json()["items"][0]["is_interaction_evidence"] is False


def test_public_endpoints_error_and_dependency_failure_codes(test_settings: Settings) -> None:
    with _build_client(test_settings) as client:
        # Paginacion fuera de limite -> 422
        assert client.get("/api/v1/taxa", params={"page_size": 500}).status_code == 422
        assert client.get("/api/v1/network/subgraph", params={"max_depth": 4}).status_code == 422
        # Entidad inexistente o DRAFT -> 404
        assert client.get("/api/v1/taxa/TAX_UNKNOWN").status_code == 404
        assert client.get("/api/v1/interactions/AST_SCI_DRAFT_001").status_code == 404
        assert client.get("/api/v1/sources/SRC_UNKNOWN").status_code == 404
        # Comparar misma localidad -> 422
        assert client.get(
            "/api/v1/network/compare-localities",
            params={"locality_id_a": "LOC_PDLG", "locality_id_b": "LOC_PDLG"},
        ).status_code == 422

    # Dependencia indispensable caida (MongoDB) -> 503
    with _build_client(test_settings, fail_mongo=True) as degraded_client:
        resp = degraded_client.get("/api/v1/taxa/TAX_AN_001")
        assert resp.status_code == 503
        assert resp.json()["error"]["code"] == "DATABASE_UNAVAILABLE"
