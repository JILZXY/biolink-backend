from __future__ import annotations

import hashlib
import json
from typing import Any

from pymongo.errors import DuplicateKeyError, PyMongoError

from biolink_backend.application.dto import (
    ImportRecordInputDTO,
    TaxonDossierClaimDTO,
    TaxonDossierDTO,
    TaxonDossierMediaDTO,
    TaxonDossierVersionDTO,
    UpdateTaxonDossierInputDTO,
)
from biolink_backend.infrastructure.mongodb import MongoConnectionProvider
from biolink_backend.shared.exceptions import (
    BusinessRuleViolationError,
    DatabaseUnavailableError,
)
from biolink_backend.shared.ids import generate_domain_id
from biolink_backend.shared.time import utc_now


class MongoAdminDossierRepository:
    """Repositorio PyMongo nativo para ingestión cruda inmutable y expedientes versionados (OP-09, OP-10, OP-13)."""

    def __init__(self, provider: MongoConnectionProvider) -> None:
        self._provider = provider

    @staticmethod
    def _compute_payload_checksum(raw_payload: dict[str, Any]) -> str:
        serialized = json.dumps(raw_payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    @staticmethod
    def _map_taxon_dossier(doc: dict[str, Any], history_count: int) -> TaxonDossierDTO:
        desc_obj = (doc.get("summary") or {}).get("description") or {}
        claims = [
            TaxonDossierClaimDTO(
                claim_id=str(c["claim_id"]),
                field=str(c["field"]),
                value=c["value"],
                source_id=str(c["source_id"]),
                source_locator=str(c["source_locator"]),
            )
            for c in doc.get("claims", [])
        ]
        media = [
            TaxonDossierMediaDTO(
                media_id=str(m["media_id"]),
                media_type=str(m["media_type"]),
                uri=str(m["uri"]),
                license=str(m["license"]),
                attribution=str(m["attribution"]),
                source_ref=str(m["source_ref"]),
                checksum=m.get("checksum"),
            )
            for m in doc.get("media", [])
        ]
        return TaxonDossierDTO(
            taxon_id=str(doc["taxon_id"]),
            schema_version=int(doc["schema_version"]),
            document_version=int(doc["document_version"]),
            history_versions_count=int(history_count),
            summary_description=desc_obj.get("value"),
            summary_source_refs=[str(x) for x in desc_obj.get("source_refs", [])],
            summary_locators=[str(x) for x in desc_obj.get("locators", [])],
            sections=dict(doc.get("sections", {})),
            claims=claims,
            media=media,
            updated_at=str(doc.get("updated_at", "")),
        )

    def insert_raw_source_records(
        self,
        *,
        import_batch_id: str,
        source_id: str,
        provider: str,
        license_id: str,
        records: list[ImportRecordInputDTO],
    ) -> dict[str, str]:
        """Almacena payloads crudos inmutables en MongoDB respetando restricciones de licencia (OP-09, RNF-04)."""
        if not records:
            return {}
        now_iso = utc_now().isoformat().replace("+00:00", "Z")
        docs_by_ext_id: dict[str, str] = {}
        try:
            db = self._provider.get_database()
            coll = db["raw_source_record"]
            for rec in records:
                raw_doc_id = f"RAW_{import_batch_id}_{rec.external_record_id}"
                checksum = self._compute_payload_checksum(rec.raw_payload)
                # Respeta RNF-08: si no permite redistribucion, se marca full_text_stored segun licencia
                doc = {
                    "_id": raw_doc_id,
                    "document_type": "raw_source_record",
                    "schema_version": 1,
                    "import_batch_id": import_batch_id,
                    "source_id": source_id,
                    "external_record_id": rec.external_record_id,
                    "provider": provider,
                    "retrieved_at": now_iso,
                    "checksum": checksum,
                    "raw_payload": dict(rec.raw_payload),
                    "license_metadata": {
                        "license_id": license_id,
                        "redistribution_permitted": bool(rec.redistribution_permitted),
                        "full_text_stored": bool(rec.full_text_stored and rec.redistribution_permitted),
                    },
                    "transformation_metadata": {
                        "normalized_at": now_iso,
                        "target_canonical_taxon_id": None,
                        "validation_status": "PENDING",
                        "warnings": [],
                    },
                    "dataset_tag": "IMPORT_BATCH",
                }
                coll.replace_one({"_id": raw_doc_id}, doc, upsert=True)
                docs_by_ext_id[rec.external_record_id] = raw_doc_id
        except PyMongoError as exc:
            raise DatabaseUnavailableError("mongodb", str(exc)) from exc
        return docs_by_ext_id

    def update_raw_source_transformation(
        self,
        *,
        raw_document_id: str,
        target_canonical_taxon_id: str | None,
        validation_status: str,
        warnings: list[str] | None = None,
    ) -> bool:
        """Actualiza unicamente transformation_metadata sin tocar raw_payload ni checksum (OP-10)."""
        now_iso = utc_now().isoformat().replace("+00:00", "Z")
        try:
            db = self._provider.get_database()
            res = db["raw_source_record"].update_one(
                {"_id": raw_document_id},
                {
                    "$set": {
                        "transformation_metadata": {
                            "normalized_at": now_iso,
                            "target_canonical_taxon_id": target_canonical_taxon_id,
                            "validation_status": validation_status,
                            "warnings": list(warnings or []),
                        }
                    }
                },
            )
            return res.matched_count > 0
        except PyMongoError as exc:
            raise DatabaseUnavailableError("mongodb", str(exc)) from exc

    def update_taxon_dossier_versioned(
        self,
        *,
        taxon_id: str,
        accepted_name: str,
        data: UpdateTaxonDossierInputDTO,
        performed_by: str,
    ) -> tuple[TaxonDossierDTO, int | None, str | None]:
        """Actualiza taxon_dossier archivando la version previa en taxon_dossier_versions (OP-13)."""
        now_iso = utc_now().isoformat().replace("+00:00", "Z")
        try:
            db = self._provider.get_database()
            dossier_coll = db["taxon_dossier"]
            versions_coll = db["taxon_dossier_versions"]

            current_doc = dossier_coll.find_one({"_id": taxon_id})
            archived_version_id: str | None = None
            previous_version: int | None = None

            if current_doc is not None:
                old_version = int(current_doc["document_version"])
                if data.expected_version is not None and data.expected_version != old_version:
                    raise BusinessRuleViolationError(
                        "RD-15",
                        f"Conflicto de version optimista para ficha de '{taxon_id}': "
                        f"esperada v{data.expected_version}, actual v{old_version}.",
                        details={
                            "taxon_id": taxon_id,
                            "expected_version": data.expected_version,
                            "current_version": old_version,
                        },
                    )
                previous_version = old_version
                new_version = old_version + 1
                created_at = current_doc.get("created_at", now_iso)
                archived_version_id = f"{taxon_id}::v{old_version}"

                snapshot_doc = dict(current_doc)
                snapshot_doc["archived_at"] = now_iso
                archive_entry = {
                    "_id": archived_version_id,
                    "taxon_id": taxon_id,
                    "document_version": old_version,
                    "archived_at": now_iso,
                    "archived_by": performed_by,
                    "change_reason": data.change_reason,
                    "snapshot": snapshot_doc,
                }
                try:
                    versions_coll.insert_one(archive_entry)
                except DuplicateKeyError:
                    # La version ya estaba archivada por un reintento; nunca sobrescribir contenido previo
                    existing_archive = versions_coll.find_one({"_id": archived_version_id})
                    if existing_archive and "snapshot" not in existing_archive:
                        versions_coll.update_one(
                            {"_id": archived_version_id},
                            {"$set": {"snapshot": snapshot_doc, "archived_by": performed_by}},
                        )
            else:
                if data.expected_version is not None and data.expected_version != 0:
                    raise BusinessRuleViolationError(
                        "RD-15",
                        f"La ficha de '{taxon_id}' aun no existe; la version esperada debe ser 0 o nula.",
                        details={"taxon_id": taxon_id, "expected_version": data.expected_version},
                    )
                new_version = 1
                created_at = now_iso

            claims_payload = [
                {
                    "claim_id": c.claim_id or generate_domain_id("taxon_name"),
                    "field": c.field,
                    "value": c.value,
                    "source_id": c.source_id,
                    "source_locator": c.source_locator,
                    "created_at": now_iso,
                }
                for c in data.claims
            ]
            media_payload = [
                {
                    "media_id": m.media_id,
                    "media_type": m.media_type,
                    "uri": m.uri,
                    "license": m.license,
                    "attribution": m.attribution,
                    "source_ref": m.source_ref,
                    **({"checksum": m.checksum} if m.checksum else {}),
                }
                for m in data.media
            ]

            new_doc = {
                "_id": taxon_id,
                "document_type": "taxon_dossier",
                "schema_version": 1,
                "document_version": new_version,
                "taxon_id": taxon_id,
                "accepted_name": accepted_name,
                "summary": {
                    "description": {
                        "value": data.summary_description,
                        "source_refs": list(data.summary_source_refs),
                        "locators": list(data.summary_locators),
                    }
                },
                "sections": dict(data.sections),
                "media": media_payload,
                "claims": claims_payload,
                "dataset_tag": data.dataset_tag,
                "created_at": created_at,
                "updated_at": now_iso,
            }

            if current_doc is not None:
                res = dossier_coll.replace_one(
                    {"_id": taxon_id, "document_version": previous_version},
                    new_doc,
                    upsert=False,
                )
                if res.matched_count == 0:
                    raise BusinessRuleViolationError(
                        "RD-15",
                        f"Concurrencia detectada al actualizar ficha de '{taxon_id}'. Intente nuevamente.",
                        details={"taxon_id": taxon_id},
                    )
            else:
                dossier_coll.insert_one(new_doc)

            history_count = int(versions_coll.count_documents({"taxon_id": taxon_id}))
            return self._map_taxon_dossier(new_doc, history_count), previous_version, archived_version_id
        except PyMongoError as exc:
            raise DatabaseUnavailableError("mongodb", str(exc)) from exc

    def get_taxon_dossier_version_history(self, taxon_id: str) -> list[TaxonDossierVersionDTO]:
        try:
            db = self._provider.get_database()
            docs = list(db["taxon_dossier_versions"].find({"taxon_id": taxon_id}).sort("document_version", 1))
        except PyMongoError as exc:
            raise DatabaseUnavailableError("mongodb", str(exc)) from exc
        return [
            TaxonDossierVersionDTO(
                version_id=str(doc["_id"]),
                taxon_id=str(doc["taxon_id"]),
                document_version=int(doc["document_version"]),
                archived_at=str(doc.get("archived_at", "")),
                archived_by=doc.get("archived_by"),
                change_reason=doc.get("change_reason"),
                snapshot=dict(doc["snapshot"]) if isinstance(doc.get("snapshot"), dict) else None,
            )
            for doc in docs
        ]

    def list_all_taxon_dossier_versions(self) -> dict[str, int]:
        """Lista todas las versiones activas en taxon_dossier para reconciliacion con PostgreSQL."""
        try:
            db = self._provider.get_database()
            docs = list(db["taxon_dossier"].find({}, {"_id": 1, "document_version": 1}))
        except PyMongoError as exc:
            raise DatabaseUnavailableError("mongodb", str(exc)) from exc
        return {str(d["_id"]): int(d["document_version"]) for d in docs}
