from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from biolink_backend.infrastructure.manager import DatabaseConnectionManager
from biolink_backend.repositories.graph.projection_repository import Neo4jProjectionRepository
from biolink_backend.security.passwords import hash_password

_BACKEND_ROOT = Path(__file__).resolve().parents[3]
_DB_DIR = _BACKEND_ROOT / "database"

DEFAULT_TEST_CREDENTIALS: dict[str, str] = {
    "USR_SYSTEM": "AdminBioLink2026!",
    "USR_CURATOR_1": "CuratorBioLink2026!",
    "USR_REVIEWER_1": "ReviewerBioLink2026!",
}

_CACHED_SEED_HASHES: dict[str, str] | None = None


def _get_seed_password_hashes() -> dict[str, str]:
    global _CACHED_SEED_HASHES
    if _CACHED_SEED_HASHES is None:
        _CACHED_SEED_HASHES = {
            uid: hash_password(pwd) for uid, pwd in DEFAULT_TEST_CREDENTIALS.items()
        }
    return _CACHED_SEED_HASHES


def apply_reference_fixture(manager: DatabaseConnectionManager) -> dict[str, int]:
    """Aplica esquema, indices y datos cientificos de referencia en los tres motores."""
    # 1. PostgreSQL: migraciones + semilla cientifica verificable
    pg_schema = (_DB_DIR / "postgresql" / "migrations" / "001_create_schema.sql").read_text(encoding="utf-8")
    pg_indexes = (_DB_DIR / "postgresql" / "migrations" / "002_create_indexes.sql").read_text(encoding="utf-8")
    pg_admin_ext = (_DB_DIR / "postgresql" / "migrations" / "003_admin_curation_extensions.sql").read_text(
        encoding="utf-8"
    )
    pg_seed = (_DB_DIR / "postgresql" / "seeds" / "001_reference_fixture.sql").read_text(encoding="utf-8")
    seed_hashes = _get_seed_password_hashes()

    with manager.postgres.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(pg_schema)
            cur.execute(pg_indexes)
            cur.execute(pg_admin_ext)
            cur.execute(pg_seed)
            for uid, pwd_hash in seed_hashes.items():
                cur.execute(
                    """
                    UPDATE app_user
                    SET password_hash = %s,
                        updated_at = clock_timestamp()
                    WHERE user_id = %s
                      AND password_hash LIKE '$2b$12$e87.dummy%%';
                    """,
                    (pwd_hash, uid),
                )
            for tid, ver in (
                ("TAX_AN_001", 2),
                ("TAX_AN_002", 1),
                ("TAX_AN_003", 1),
                ("TAX_PL_020", 1),
                ("TAX_PL_021", 1),
            ):
                cur.execute(
                    """
                    UPDATE taxon
                    SET dossier_version = %s,
                        dossier_sync_status = 'SYNCED'
                    WHERE taxon_id = %s
                      AND dossier_version = 0;
                    """,
                    (ver, tid),
                )
            conn.commit()

            cur.execute(
                """
                SELECT
                    a.assertion_id, a.actor_taxon_id, a.target_taxon_id,
                    it.code AS interaction_type_code, a.locality_id,
                    a.period_start, a.period_end, a.season_label,
                    a.method_code, a.assertion_scope, a.status,
                    act.accepted_name AS actor_name, act.taxonomic_rank AS actor_rank,
                    tgt.accepted_name AS target_name, tgt.taxonomic_rank AS target_rank,
                    l.name AS locality_name, l.locality_type, l.protected_status, l.spatial_validation_status,
                    e.evidence_id, e.evidence_kind, e.source_locator,
                    s.source_id, s.source_type, s.title AS source_title, s.publication_year, s.authors,
                    s.citation_text, s.doi, s.uri, s.license_id
                FROM interaction_assertion a
                JOIN interaction_type it ON it.interaction_type_id = a.interaction_type_id
                JOIN taxon act ON act.taxon_id = a.actor_taxon_id
                JOIN taxon tgt ON tgt.taxon_id = a.target_taxon_id
                JOIN locality l ON l.locality_id = a.locality_id
                JOIN assertion_evidence ae ON ae.assertion_id = a.assertion_id AND ae.evidence_role = 'PRIMARY'
                JOIN evidence e ON e.evidence_id = ae.evidence_id
                JOIN source s ON s.source_id = e.source_id
                WHERE a.status = 'PUBLISHED'
                ORDER BY a.assertion_id;
                """
            )
            published_rows = cur.fetchall()

            cur.execute("SELECT taxon_id, accepted_name, taxonomic_rank, organism_group FROM taxon ORDER BY taxon_id;")
            taxa_rows = cur.fetchall()

            cur.execute("SELECT source_id, source_type, title, authors, publication_year, doi, uri, license_id FROM source;")
            source_rows = cur.fetchall()

            cur.execute("SELECT locality_id, name, locality_type, protected_status, spatial_validation_status FROM locality;")
            locality_rows = cur.fetchall()

    # 2. MongoDB: colecciones con $jsonSchema, indices y documentos de referencia
    mongo_db = manager.mongodb.get_database()
    schemas_dir = _DB_DIR / "mongodb" / "schemas"
    for coll_name, schema_file in (
        ("taxon_dossier", "taxon_dossier.schema.json"),
        ("study_dossier", "study_dossier.schema.json"),
        ("raw_source_record", "raw_source_record.schema.json"),
    ):
        validator = json.loads((schemas_dir / schema_file).read_text(encoding="utf-8"))
        if coll_name in mongo_db.list_collection_names():
            mongo_db.command("collMod", coll_name, validator=validator, validationLevel="strict", validationAction="error")
        else:
            mongo_db.create_collection(coll_name, validator=validator, validationLevel="strict", validationAction="error")

    mongo_db["taxon_dossier"].create_index([("accepted_name", 1)], name="idx_taxon_dossier_name")
    mongo_db["taxon_dossier"].create_index([("claims.field", 1), ("claims.value", 1)], name="idx_taxon_dossier_claims_multikey")
    mongo_db["taxon_dossier_versions"].create_index([("taxon_id", 1), ("document_version", 1)], unique=True)
    mongo_db["study_dossier"].create_index([("source_id", 1)], unique=True, name="uq_study_dossier_source_id")
    mongo_db["raw_source_record"].create_index([("import_batch_id", 1), ("external_record_id", 1)], unique=True)

    # Expedientes de taxones clave (con historial de versiones para TAX_AN_001)
    taxa_by_id = {r["taxon_id"]: r for r in taxa_rows}
    for tid in ("TAX_AN_001", "TAX_AN_002", "TAX_AN_003", "TAX_PL_020", "TAX_PL_021"):
        t = taxa_by_id[tid]
        doc = {
            "_id": tid,
            "document_type": "taxon_dossier",
            "schema_version": 1,
            "document_version": 2 if tid == "TAX_AN_001" else 1,
            "taxon_id": tid,
            "accepted_name": t["accepted_name"],
            "summary": {
                "description": {
                    "value": f"Ficha cientifica documentada para {t['accepted_name']} en literatura de Montes Azules.",
                    "source_refs": ["BAT2007" if tid.startswith("TAX_AN_00") and int(tid[-3:]) <= 3 else "MAMMAL2016"],
                    "locators": ["Olea-Wagner et al. (2007)" if tid in ("TAX_AN_001", "TAX_AN_002", "TAX_AN_003") else "Camargo-Sanabria & Mendoza (2016)"],
                }
            },
            "sections": {
                "morphology": [],
                "ecology": {
                    "habitats": ["Selva alta perennifolia"],
                    "diet": ["Frugivoria documentada en excretas"] if t["organism_group"] == "ANIMAL" else [],
                    "activity_patterns": ["Nocturno"] if tid in ("TAX_AN_001", "TAX_AN_002", "TAX_AN_003") else [],
                    "notes": [],
                },
                "conservation": [],
                "distribution": ["Montes Azules (sector sur)"],
            },
            "media": [],
            "claims": [
                {
                    "claim_id": f"CLM_{tid}_1",
                    "field": "taxonomy.verbatim_reference",
                    "value": t["accepted_name"],
                    "source_id": "BAT2007" if tid in ("TAX_AN_001", "TAX_AN_002", "TAX_AN_003") else "MAMMAL2016",
                    "source_locator": "Etapa 1 — Seccion 4",
                }
            ],
            "dataset_tag": "SCIENTIFIC_FIXTURE",
            "created_at": "2026-11-20T00:00:00Z",
            "updated_at": "2026-11-21T00:00:00Z",
        }
        mongo_db["taxon_dossier"].replace_one({"_id": tid}, doc, upsert=True)

    v1_snapshot = {
        "_id": "TAX_AN_001",
        "document_type": "taxon_dossier",
        "schema_version": 1,
        "document_version": 1,
        "taxon_id": "TAX_AN_001",
        "accepted_name": "Artibeus lituratus",
        "summary": {
            "description": {
                "value": "Registro inicial de Artibeus lituratus previo a incorporacion de notas de dieta.",
                "source_refs": ["BAT2007"],
                "locators": ["Olea-Wagner et al. (2007)"],
            }
        },
        "sections": {
            "morphology": [],
            "ecology": {"habitats": ["Selva alta perennifolia"], "diet": [], "activity_patterns": ["Nocturno"], "notes": []},
            "conservation": [],
            "distribution": ["Montes Azules (sector sur)"],
        },
        "media": [],
        "claims": [],
        "dataset_tag": "SCIENTIFIC_FIXTURE",
        "created_at": "2026-11-20T00:00:00Z",
        "updated_at": "2026-11-20T00:00:00Z",
    }
    mongo_db["taxon_dossier_versions"].replace_one(
        {"_id": "TAX_AN_001::v1"},
        {
            "_id": "TAX_AN_001::v1",
            "taxon_id": "TAX_AN_001",
            "document_version": 1,
            "archived_at": "2026-11-21T00:00:00Z",
            "archived_by": "USR_CURATOR_1",
            "change_reason": "Incorporacion de dieta frugivora documentada en excretas",
            "snapshot": v1_snapshot,
        },
        upsert=True,
    )

    for src in source_rows:
        sid = src["source_id"]
        is_bat = sid == "BAT2007"
        study_doc = {
            "_id": f"STD_{sid}",
            "document_type": "study_dossier",
            "schema_version": 1,
            "source_id": sid,
            "doi_or_uri": src["doi"] or src["uri"],
            "research_question": (
                "Diversidad de frutos consumidos por tres especies de murcielagos filostomidos en selva lacandona."
                if is_bat
                else "Interacciones entre mamiferos terrestres y frutos de Licania platypus y Pouteria sapota."
            ),
            "sampling_design": {
                "effort": "207 muestras fecales de 115 individuos" if is_bat else "278 dias-camara en 13 arboles focales",
                "units": "Muestras de excretas" if is_bat else "Eventos fotograficos independientes",
                "instruments": ["Redes de niebla / identificacion de semillas"] if is_bat else ["Camaras trampa"],
                "methods": ["FECAL_SEED_IDENTIFICATION"] if is_bat else ["CAMERA_TRAP"],
            },
            "study_localities": ["LOC_RBMA_SOUTH", "LOC_PDLG"] if is_bat else ["LOC_RBMA_SOUTH"],
            "study_period": {
                "period_start": "2004-01" if is_bat else "2013",
                "period_end": "2004-07" if is_bat else "2014",
                "temporal_notes": "Epoca seca y lluviosa 2004" if is_bat else "Comparacion 2013-2014",
            },
            "focal_taxa": ["TAX_AN_001", "TAX_AN_002", "TAX_AN_003"] if is_bat else ["TAX_PL_020", "TAX_PL_021"],
            "interpretation_limits": [
                "No demuestra germinacion ni establecimiento efectivo de plantulas (RD-05)."
                if is_bat
                else "La categoria agrupa consumo o remocion de frutos; no equivale a dispersion efectiva (RD-04, RD-05)."
            ],
            "license_and_usage": {
                "license_id": src["license_id"],
                "can_redistribute_fulltext": False,
                "attribution": src["authors"],
            },
            "checksum": hashlib.sha256(sid.encode("utf-8")).hexdigest(),
            "dataset_tag": "SCIENTIFIC_FIXTURE",
            "created_at": "2026-11-20T00:00:00Z",
        }
        mongo_db["study_dossier"].replace_one({"_id": study_doc["_id"]}, study_doc, upsert=True)

    # Registros crudos de referencia respetando licencia restrictiva (ACADEMIC_CITATION_ONLY -> redistribution_permitted=False)
    for row in published_rows[:2]:
        aid = row["assertion_id"]
        raw_doc = {
            "_id": f"RAW_{aid}",
            "document_type": "raw_source_record",
            "schema_version": 1,
            "import_batch_id": "BAT_REF_001",
            "source_id": row["source_id"],
            "external_record_id": aid,
            "provider": row["source_id"],
            "retrieved_at": "2026-11-20T00:00:00Z",
            "checksum": hashlib.sha256(aid.encode("utf-8")).hexdigest(),
            "raw_payload": {"assertion_id": aid, "source_locator": row["source_locator"]},
            "license_metadata": {
                "license_id": row["license_id"],
                "redistribution_permitted": False,
                "full_text_stored": False,
                "attribution": row["authors"],
            },
            "transformation_metadata": {
                "normalized_at": "2026-11-20T00:00:00Z",
                "target_canonical_taxon_id": row["actor_taxon_id"],
                "validation_status": "VALID",
                "warnings": [],
            },
            "dataset_tag": "SCIENTIFIC_FIXTURE",
        }
        mongo_db["raw_source_record"].replace_one({"_id": raw_doc["_id"]}, raw_doc, upsert=True)

    # 3. Neo4j: constraints, indices, metadatos de localidades/taxones y proyeccion de PUBLISHED unicamente
    constraints_cypher = (_DB_DIR / "neo4j" / "constraints" / "001_create_constraints.cypher").read_text(encoding="utf-8")
    indexes_cypher = (_DB_DIR / "neo4j" / "indexes" / "001_create_indexes.cypher").read_text(encoding="utf-8")
    with manager.neo4j.session() as session:
        for stmt in constraints_cypher.split(";"):
            cleaned = "\n".join(line for line in stmt.splitlines() if not line.strip().startswith("//")).strip()
            if cleaned:
                session.run(cleaned)
        for stmt in indexes_cypher.split(";"):
            cleaned = "\n".join(line for line in stmt.splitlines() if not line.strip().startswith("//")).strip()
            if cleaned:
                session.run(cleaned)

        # Asegurar que los nodos Locality y Taxon tengan nombre y protected_status actualizados
        for loc in locality_rows:
            session.run(
                """
                MERGE (l:Locality {locality_id: $locality_id})
                SET l.name = $name,
                    l.locality_type = $locality_type,
                    l.protected_status = $protected_status,
                    l.spatial_validation_status = $spatial_validation_status;
                """,
                dict(loc),
            )

    proj_repo = Neo4jProjectionRepository(manager.neo4j)
    for row in published_rows:
        payload: dict[str, Any] = {
            "assertion_id": row["assertion_id"],
            "actor_taxon_id": row["actor_taxon_id"],
            "target_taxon_id": row["target_taxon_id"],
            "interaction_type_code": row["interaction_type_code"],
            "locality_id": row["locality_id"],
            "period_start": row["period_start"],
            "period_end": row["period_end"],
            "season_label": row["season_label"],
            "method_code": row["method_code"],
            "assertion_scope": row["assertion_scope"],
            "status": row["status"],
            "evidence_ids": [row["evidence_id"]],
            "evidence_kind": row["evidence_kind"],
            "source_locator": row["source_locator"],
            "source_id": row["source_id"],
            "source_type": row["source_type"],
            "source_title": row["source_title"],
            "publication_year": row["publication_year"],
            "actor_name": row["actor_name"],
            "target_name": row["target_name"],
        }
        proj_repo.project_assertion(payload)

    published_ids = [row["assertion_id"] for row in published_rows]
    if published_ids:
        with manager.postgres.connection() as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE interaction_assertion
                        SET projection_status = 'SYNCED',
                            last_projected_at = COALESCE(last_projected_at, clock_timestamp())
                        WHERE assertion_id = ANY(%s)
                          AND status = 'PUBLISHED'
                          AND projection_status IN ('NOT_APPLICABLE', 'PENDING');
                        """,
                        (published_ids,),
                    )
                    cur.execute(
                        """
                        UPDATE outbox_event
                        SET status = 'PROCESSED',
                            processed_at = COALESCE(processed_at, clock_timestamp()),
                            error_message = NULL
                        WHERE outbox_event_id LIKE 'OBX_AST_SCI_%%'
                          AND status = 'PENDING';
                        """
                    )

    return {
        "taxa_count": len(taxa_rows),
        "published_assertions_count": len(published_rows),
        "sources_count": len(source_rows),
    }
