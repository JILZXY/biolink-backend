from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from biolink_backend.config.settings import Settings
from biolink_backend.infrastructure.manager import DatabaseConnectionManager
from biolink_backend.infrastructure.reference_seeder import (
    DEFAULT_TEST_CREDENTIALS,
    apply_reference_fixture,
)
from biolink_backend.main import create_app
from biolink_backend.repositories.graph.projection_repository import Neo4jProjectionRepository
from biolink_backend.repositories.relational.outbox_repository import PostgresOutboxRepository
from biolink_backend.workers.outbox_worker import OutboxProjectionWorker
from tests.test_public_queries_live import _find_free_port

_BACKEND_DIR = Path(__file__).resolve().parent.parent
_COMPOSE_FILE = _BACKEND_DIR / "docker-compose.yaml"
_ENV_EXAMPLE = _BACKEND_DIR / ".env.example"


@pytest.mark.live
def test_live_admin_workflow_rbac_outbox_and_reconciliation() -> None:
    """Valida OP-09..OP-14, RBAC, Outbox Worker y Reconciliacion sobre PG 18.7, Mongo 8.0.38 y Neo4j 2026.10.2."""
    env_file = _BACKEND_DIR / ".env"
    created_temp_env = False
    if not env_file.exists():
        env_file.write_text(_ENV_EXAMPLE.read_text(encoding="utf-8"), encoding="utf-8")
        created_temp_env = True

    pg_port = _find_free_port(5436)
    mongo_port = _find_free_port(27021)
    neo_bolt_port = _find_free_port(7692)
    neo_http_port = _find_free_port(7478)

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
        "docker", "compose", "--env-file", str(env_file), "-f", str(_COMPOSE_FILE),
        "up", "-d", "--wait", "relational-db", "document-db", "graph-db",
    ]
    down_cmd = [
        "docker", "compose", "--env-file", str(env_file), "-f", str(_COMPOSE_FILE),
        "down", "-v",
    ]

    try:
        proc = subprocess.run(up_cmd, cwd=str(_BACKEND_DIR), env=compose_env, capture_output=True, text=True, check=False)
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
            apply_reference_fixture(manager)
        finally:
            manager.shutdown()

        app = create_app(live_settings)
        with TestClient(app) as client:
            # 1. Autenticacion de Curador y Revisor reales contra app_user en PostgreSQL
            cur_login = client.post(
                "/api/v1/auth/login",
                json={"email": "curador1@biolink.org", "password": DEFAULT_TEST_CREDENTIALS["USR_CURATOR_1"]},
            )
            assert cur_login.status_code == 200
            cur_token = cur_login.json()["access_token"]
            assert cur_login.cookies.get(live_settings.session_cookie_name) == cur_token

            rev_login = client.post(
                "/api/v1/auth/login",
                json={"email": "revisora1@biolink.org", "password": DEFAULT_TEST_CREDENTIALS["USR_REVIEWER_1"]},
            )
            assert rev_login.status_code == 200
            rev_token = rev_login.json()["access_token"]

            cur_hdrs = {"Authorization": f"Bearer {cur_token}"}
            rev_hdrs = {"Authorization": f"Bearer {rev_token}"}

            # 2. OP-09: Registro de lote de importacion con raw_source_record inmutable
            batch_resp = client.post(
                "/api/v1/admin/import-batches",
                headers=cur_hdrs,
                json={
                    "source_id": "BAT2007",
                    "provider_version": "live-test-2026-11",
                    "endpoint_or_file": "exports/live_test_batch.json",
                    "provider": "LITERATURE",
                    "query_parameters": {"locality": "RBMA"},
                    "filters": {"method": "FECAL_SEED_IDENTIFICATION"},
                    "records": [
                        {
                            "external_record_id": "LIVE_REC_001",
                            "verbatim_name": "Artibeus lituratus palmarum",
                            "proposed_external_scheme": "GBIF",
                            "proposed_external_id": "2432943",
                            "raw_payload": {"species": "Artibeus lituratus palmarum", "fruit": "Ficus insipida"},
                        },
                        {
                            "external_record_id": "LIVE_REC_002",
                            "verbatim_name": "Murcielago sp. indet.",
                            "raw_payload": {"species": "Murcielago sp. indet."},
                        },
                    ],
                },
            )
            assert batch_resp.status_code == 201
            batch_id = batch_resp.json()["batch"]["import_batch_id"]
            recs = batch_resp.json()["records"]
            rec1_id = recs[0]["import_record_id"]
            rec2_id = recs[1]["import_record_id"]
            raw1_id = recs[0]["raw_document_id"]

            # 3. OP-10: Normalizar uno VALID y otro UNRESOLVED sin mutar raw_payload
            norm1 = client.patch(
                f"/api/v1/admin/import-records/{rec1_id}/normalize",
                headers=cur_hdrs,
                json={
                    "validation_status": "VALID",
                    "canonical_taxon_id": "TAX_AN_001",
                    "matching_rule": "EXACT_SPECIES_CANONICAL",
                    "validation_notes": "Resuelto a Artibeus lituratus",
                },
            )
            assert norm1.status_code == 200
            assert norm1.json()["raw_document_updated"] is True

            norm2 = client.patch(
                f"/api/v1/admin/import-records/{rec2_id}/normalize",
                headers=cur_hdrs,
                json={
                    "validation_status": "UNRESOLVED",
                    "matching_rule": "UNSPECIFIED_GENUS",
                    "validation_notes": "Pendiente de verificacion taxonomica",
                },
            )
            assert norm2.status_code == 200
            unresolved_list = client.get(
                "/api/v1/admin/import-records",
                params={"import_batch_id": batch_id, "validation_status": "UNRESOLVED"},
                headers=cur_hdrs,
            )
            assert unresolved_list.status_code == 200
            assert len(unresolved_list.json()) == 1

            # Verificar inmutabilidad real de raw_payload en MongoDB
            mongo_raw = app.state.db_manager.mongodb.get_database()["raw_source_record"].find_one({"_id": raw1_id})
            assert mongo_raw is not None
            assert mongo_raw["raw_payload"] == {"species": "Artibeus lituratus palmarum", "fruit": "Ficus insipida"}
            assert mongo_raw["transformation_metadata"]["target_canonical_taxon_id"] == "TAX_AN_001"

            # 4. OP-11: Crear afirmacion en DRAFT -> enviar a IN_REVIEW; verificar exclusion publica
            created_ast = client.post(
                "/api/v1/admin/assertions",
                headers=cur_hdrs,
                json={
                    "actor_taxon_id": "TAX_AN_002",
                    "target_taxon_id": "TAX_PL_003",
                    "interaction_type_code": "CONSUMES_FRUIT_OF",
                    "locality_id": "LOC_RBMA_SOUTH",
                    "period_start": "2004-01",
                    "period_end": "2004-07",
                    "season_label": "Dry and rainy 2004",
                    "method_code": "FECAL_SEED_IDENTIFICATION",
                    "initial_status": "DRAFT",
                    "evidences": [
                        {
                            "source_id": "BAT2007",
                            "source_locator": "Olea-Wagner et al. (2007), Live Validation Case",
                            "summary": "Carollia perspicillata consuming Ficus insipida",
                            "interpretation_limit": "Does not prove seedling establishment",
                        }
                    ],
                },
            )
            assert created_ast.status_code == 201
            new_aid = created_ast.json()["assertion_id"]
            assert client.get(f"/api/v1/interactions/{new_aid}").status_code == 404

            sub_ast = client.post(
                f"/api/v1/admin/assertions/{new_aid}/submit",
                headers=cur_hdrs,
                json={"reason": "Lista para dictamen"},
            )
            assert sub_ast.status_code == 200
            assert sub_ast.json()["assertion"]["status"] == "IN_REVIEW"

            # RBAC real: Curador no puede aprobar -> 403
            forbidden = client.post(
                f"/api/v1/admin/assertions/{new_aid}/review",
                headers=cur_hdrs,
                json={"action": "APPROVE", "reason": "No autorizado"},
            )
            assert forbidden.status_code == 403

            # 5. OP-12: Revisor aprueba -> PG PUBLISHED + projection_status=PENDING + Outbox PENDING
            approved = client.post(
                f"/api/v1/admin/assertions/{new_aid}/review",
                headers=rev_hdrs,
                json={"action": "APPROVE", "reason": "Evidencia verificada por revisor"},
            )
            assert approved.status_code == 200
            assert approved.json()["assertion"]["status"] == "PUBLISHED"
            assert approved.json()["assertion"]["projection_status"] == "PENDING"
            assert approved.json()["outbox_event"]["processing_status"] == "PENDING"

            # Antes del worker, OP-04 indica graph_projected = False honestamente
            before_worker = client.get(f"/api/v1/interactions/{new_aid}")
            assert before_worker.status_code == 200
            assert before_worker.json()["graph_projected"] is False

            # Ejecutar worker Outbox real contra PostgreSQL y Neo4j
            worker = OutboxProjectionWorker(
                outbox_repo=PostgresOutboxRepository(app.state.db_manager.postgres),
                graph_repo=Neo4jProjectionRepository(app.state.db_manager.neo4j),
            )
            assert worker.process_once() == 1

            after_worker = client.get(f"/api/v1/interactions/{new_aid}")
            assert after_worker.status_code == 200
            assert after_worker.json()["graph_projected"] is True

            admin_after = client.get(f"/api/v1/admin/assertions/{new_aid}", headers=rev_hdrs)
            assert admin_after.status_code == 200
            assert admin_after.json()["projection_status"] == "SYNCED"

            # 6. Retiro de afirmacion -> worker elimina nodo en Neo4j
            withdrawn = client.post(
                f"/api/v1/admin/assertions/{new_aid}/review",
                headers=rev_hdrs,
                json={"action": "WITHDRAW", "reason": "Retiro de prueba controlada"},
            )
            assert withdrawn.status_code == 200
            assert withdrawn.json()["outbox_event"]["event_type"] == "ASSERTION_WITHDRAWN"
            assert worker.process_once() == 1
            assert client.get(f"/api/v1/interactions/{new_aid}").status_code == 404

            # 7. OP-13: Actualizacion versionada de expediente TAX_AN_001 -> v3 y archivo v2
            dossier_upd = client.put(
                "/api/v1/admin/taxa/TAX_AN_001/dossier",
                headers=cur_hdrs,
                json={
                    "summary_description": "Ficha actualizada v3 en validacion en vivo.",
                    "summary_source_refs": ["BAT2007"],
                    "summary_locators": ["Olea-Wagner et al. (2007), Table 2"],
                    "sections": {
                        "morphology": [],
                        "ecology": {
                            "habitats": ["Selva alta perennifolia"],
                            "diet": ["Ficus", "Cecropia obtusifolia", "Cecropia peltata"],
                            "activity_patterns": ["Nocturno"],
                            "notes": [],
                        },
                        "conservation": [],
                        "distribution": ["Montes Azules (sector sur)", "Ejido Playon de la Gloria"],
                    },
                    "claims": [
                        {
                            "claim_id": "CLM_LIVE_1",
                            "field": "ecology.diet",
                            "value": "Cecropia obtusifolia",
                            "source_id": "BAT2007",
                            "source_locator": "Table 2",
                        }
                    ],
                    "change_reason": "Agregar distribucion y dieta verificada",
                    "expected_version": 2,
                },
            )
            assert dossier_upd.status_code == 200
            assert dossier_upd.json()["previous_version"] == 2
            assert dossier_upd.json()["archived_version_id"] == "TAX_AN_001::v2"
            assert dossier_upd.json()["dossier"]["document_version"] == 3
            assert dossier_upd.json()["dossier"]["history_versions_count"] == 2

            # 8. OP-14: Historial completo de procedencia
            prov_ast = client.get(f"/api/v1/admin/provenance/assertion/{new_aid}", headers=rev_hdrs)
            assert prov_ast.status_code == 200
            assert len(prov_ast.json()["review_events"]) == 3
            assert len(prov_ast.json()["outbox_events"]) == 2
            assert len(prov_ast.json()["audit_logs"]) >= 3

            prov_tax = client.get("/api/v1/admin/provenance/taxon/TAX_AN_001", headers=cur_hdrs)
            assert prov_tax.status_code == 200
            assert len(prov_tax.json()["dossier_versions"]) == 2

            # 9. Reconciliacion tras desincronizacion inducida en Neo4j y PostgreSQL
            with app.state.db_manager.neo4j.session() as neo_s:
                neo_s.run("MATCH (a:InteractionAssertion {assertion_id: 'AST_SCI_001'}) DETACH DELETE a")
            assert client.get("/api/v1/interactions/AST_SCI_001").json()["graph_projected"] is False

            with app.state.db_manager.postgres.connection() as pg_c:
                with pg_c.transaction():
                    pg_c.execute(
                        "UPDATE taxon SET dossier_version = 1, dossier_sync_status = 'PENDING_RECONCILIATION' WHERE taxon_id = 'TAX_AN_001'"
                    )

            rec_resp = client.post("/api/v1/admin/reconcile", headers=rev_hdrs)
            assert rec_resp.status_code == 200
            rec_body = rec_resp.json()
            assert rec_body["graph_expected_published_count"] == 21
            assert rec_body["graph_projected_or_verified_count"] == 21
            assert "TAX_AN_001" in rec_body["reconciled_taxon_ids"]
            assert client.get("/api/v1/interactions/AST_SCI_001").json()["graph_projected"] is True

            # 10. Logout revoca sesion
            logout = client.post("/api/v1/auth/logout", headers=cur_hdrs)
            assert logout.status_code == 200
            assert logout.json()["revoked"] is True
            assert client.get("/api/v1/auth/me", headers=cur_hdrs).status_code == 401
    finally:
        subprocess.run(down_cmd, cwd=str(_BACKEND_DIR), env=compose_env, capture_output=True, text=True, check=False)
        if created_temp_env and env_file.exists():
            os.remove(env_file)
