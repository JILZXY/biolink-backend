from __future__ import annotations

import os
import socket
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from biolink_backend.config.settings import Settings
from biolink_backend.infrastructure.manager import DatabaseConnectionManager
from biolink_backend.infrastructure.reference_seeder import apply_reference_fixture
from biolink_backend.main import create_app

_BACKEND_DIR = Path(__file__).resolve().parent.parent
_COMPOSE_FILE = _BACKEND_DIR / "docker-compose.yaml"
_ENV_EXAMPLE = _BACKEND_DIR / ".env.example"


def _find_free_port(preferred: int) -> int:
    for candidate in (preferred, 0):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind(("127.0.0.1", candidate))
                return int(sock.getsockname()[1])
            except OSError:
                continue
    raise RuntimeError(f"No se pudo reservar puerto libre cerca de {preferred}")


@pytest.mark.live
def test_live_all_nine_public_operations_with_scientific_fixture() -> None:
    """Valida OP-01..OP-08 y OP-15 contra PostgreSQL 18.7, MongoDB 8.0.38 y Neo4j 2026.10.2 reales."""
    env_file = _BACKEND_DIR / ".env"
    created_temp_env = False
    if not env_file.exists():
        env_file.write_text(_ENV_EXAMPLE.read_text(encoding="utf-8"), encoding="utf-8")
        created_temp_env = True

    pg_port = _find_free_port(5435)
    mongo_port = _find_free_port(27020)
    neo_bolt_port = _find_free_port(7691)
    neo_http_port = _find_free_port(7477)

    compose_env = os.environ.copy()
    compose_env.update(
        {
            "POSTGRES_PORT": str(pg_port),
            "MONGODB_PORT": str(mongo_port),
            "NEO4J_BOLT_PORT": str(neo_bolt_port),
            "NEO4J_HTTP_PORT": str(neo_http_port),
        }
    )

    up_cmd = [
        "docker",
        "compose",
        "--env-file",
        str(env_file),
        "-f",
        str(_COMPOSE_FILE),
        "up",
        "-d",
        "--wait",
        "relational-db",
        "document-db",
        "graph-db",
    ]
    down_cmd = [
        "docker",
        "compose",
        "--env-file",
        str(env_file),
        "-f",
        str(_COMPOSE_FILE),
        "down",
        "-v",
    ]

    try:
        proc = subprocess.run(
            up_cmd,
            cwd=str(_BACKEND_DIR),
            env=compose_env,
            capture_output=True,
            text=True,
            check=False,
        )
        assert proc.returncode == 0, f"Error levantando contenedores: {proc.stderr or proc.stdout}"

        live_settings = Settings(
            _env_file=str(env_file),
            app_env="test",
            postgres_port=pg_port,
            mongodb_port=mongo_port,
            neo4j_bolt_port=neo_bolt_port,
            neo4j_http_port=neo_http_port,
        )
        manager = DatabaseConnectionManager(live_settings)
        manager.startup()
        try:
            seed_summary = apply_reference_fixture(manager)
            assert seed_summary["taxa_count"] == 32
            assert seed_summary["published_assertions_count"] == 21
        finally:
            manager.shutdown()

        app = create_app(live_settings)
        with TestClient(app) as client:
            # Readiness real de los 3 motores
            ready_resp = client.get("/api/v1/health/ready")
            assert ready_resp.status_code == 200
            assert ready_resp.json()["ready"] is True

            # OP-01: Sinonimo, identificador externo y rango GENUS preservado
            op1_syn = client.get("/api/v1/taxa", params={"q": "Sturnira parvidens"})
            assert op1_syn.status_code == 200
            assert op1_syn.json()["items"][0]["taxon_id"] == "TAX_AN_003"
            assert op1_syn.json()["items"][0]["matched_usage"] == "SYNONYM"

            op1_ext = client.get("/api/v1/taxa", params={"q": "REF:BAT2007:TAX_AN_001"})
            assert op1_ext.status_code == 200
            assert op1_ext.json()["items"][0]["taxon_id"] == "TAX_AN_001"

            op1_genus = client.get("/api/v1/taxa", params={"q": "Ficus sp."})
            assert op1_genus.status_code == 200
            assert op1_genus.json()["items"][0]["taxonomic_rank"] == "GENUS"

            # OP-02: Identidad PG + taxon_dossier MongoDB con historial de versiones
            op2 = client.get("/api/v1/taxa/TAX_AN_001")
            assert op2.status_code == 200
            assert op2.json()["has_dossier"] is True
            assert op2.json()["dossier"]["document_version"] == 2
            assert op2.json()["dossier"]["history_versions_count"] == 1

            # OP-03: 4 interacciones PUBLISHED de Artibeus lituratus; cero DRAFT
            op3 = client.get("/api/v1/taxa/TAX_AN_001/interactions")
            assert op3.status_code == 200
            op3_body = op3.json()
            assert op3_body["pagination"]["total"] == 4
            assert all(item["status"] == "PUBLISHED" for item in op3_body["items"])
            assert all(item["assertion_id"] != "AST_SCI_DRAFT_001" for item in op3_body["items"])

            # OP-04: Evidencia trazable + study_dossier + licencia restrictiva; DRAFT -> 404
            op4 = client.get("/api/v1/interactions/AST_SCI_001")
            assert op4.status_code == 200
            op4_body = op4.json()
            assert op4_body["graph_projected"] is True
            assert len(op4_body["evidences"]) == 1
            assert op4_body["study_dossiers"][0]["source_id"] == "BAT2007"
            assert op4_body["raw_source_references"][0]["redistribution_permitted"] is False
            assert op4_body["raw_source_references"][0]["raw_payload"] is None

            assert client.get("/api/v1/interactions/AST_SCI_DRAFT_001").status_code == 404

            # OP-05: Subred filtrada por localidad sin mezclar PDLG ni indeterminadas
            op5 = client.get("/api/v1/network/subgraph", params={"locality_id": "LOC_RBMA_SOUTH"})
            assert op5.status_code == 200
            assert len(op5.json()["interactions"]) == 18
            assert all(e["locality_id"] == "LOC_RBMA_SOUTH" for e in op5.json()["interactions"])

            # OP-06: Montes Azules vs Playon de la Gloria separados
            op6 = client.get(
                "/api/v1/network/compare-localities",
                params={"locality_id_a": "LOC_RBMA_SOUTH", "locality_id_b": "LOC_PDLG"},
            )
            assert op6.status_code == 200
            op6_body = op6.json()
            assert op6_body["locality_a"]["published_assertions_count"] == 18
            assert op6_body["locality_b"]["published_assertions_count"] == 3
            assert op6_body["locality_a"]["locality"]["spatial_validation_status"] == "INSIDE_ANP"
            assert op6_body["locality_b"]["locality"]["spatial_validation_status"] == "OUTSIDE_ANP"

            # OP-07: Conectividad descriptiva (2 plantas distintas en 4 afirmaciones)
            op7 = client.get("/api/v1/taxa/TAX_AN_001/connectivity")
            assert op7.status_code == 200
            op7_body = op7.json()
            assert op7_body["distinct_partner_taxa_count"] == 2
            assert op7_body["documented_assertions_count"] == 4

            # OP-08: Fuente BAT2007 con version de dataset y study_dossier
            op8 = client.get("/api/v1/sources/BAT2007")
            assert op8.status_code == 200
            assert len(op8.json()["versions"]) == 1
            assert op8.json()["study_dossier"]["source_id"] == "BAT2007"

            # OP-15: Ocurrencias independientes de interacciones
            op15 = client.get("/api/v1/occurrences", params={"taxon_id": "TAX_AN_011"})
            assert op15.status_code == 200
            assert op15.json()["pagination"]["total"] == 1
            assert op15.json()["items"][0]["is_interaction_evidence"] is False
    finally:
        subprocess.run(down_cmd, cwd=str(_BACKEND_DIR), env=compose_env, capture_output=True, text=True, check=False)
        if created_temp_env and env_file.exists():
            os.remove(env_file)
