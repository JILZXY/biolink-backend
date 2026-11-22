from __future__ import annotations

from pymongo.errors import PyMongoError

from biolink_backend.application.dto import (
    RawSourceReferenceDTO,
    StudyDossierDTO,
    TaxonDossierClaimDTO,
    TaxonDossierDTO,
    TaxonDossierMediaDTO,
)
from biolink_backend.infrastructure.mongodb import MongoConnectionProvider
from biolink_backend.shared.exceptions import DatabaseUnavailableError


class MongoPublicDossierRepository:
    """Repositorio PyMongo nativo para expedientes flexibles y payloads crudos."""

    def __init__(self, provider: MongoConnectionProvider) -> None:
        self._provider = provider

    def get_taxon_dossier(self, taxon_id: str) -> TaxonDossierDTO | None:
        try:
            db = self._provider.get_database()
            doc = db["taxon_dossier"].find_one({"_id": taxon_id})
            if not doc:
                return None
            history_count = db["taxon_dossier_versions"].count_documents({"taxon_id": taxon_id})
        except PyMongoError as exc:
            raise DatabaseUnavailableError("mongodb", str(exc)) from exc

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

    @staticmethod
    def _map_study_dossier(doc: dict) -> StudyDossierDTO:
        return StudyDossierDTO(
            dossier_id=str(doc["_id"]),
            source_id=str(doc["source_id"]),
            schema_version=int(doc["schema_version"]),
            doi_or_uri=doc.get("doi_or_uri"),
            research_question=str(doc["research_question"]),
            sampling_design=dict(doc.get("sampling_design", {})),
            study_localities=[str(x) for x in doc.get("study_localities", [])],
            study_period=dict(doc.get("study_period", {})),
            focal_taxa=[str(x) for x in doc.get("focal_taxa", [])],
            interpretation_limits=[str(x) for x in doc.get("interpretation_limits", [])],
            license_and_usage=dict(doc.get("license_and_usage", {})),
            checksum=doc.get("checksum"),
        )

    def get_study_dossier(self, source_id: str) -> StudyDossierDTO | None:
        try:
            db = self._provider.get_database()
            doc = db["study_dossier"].find_one({"source_id": source_id})
        except PyMongoError as exc:
            raise DatabaseUnavailableError("mongodb", str(exc)) from exc
        if not doc:
            return None
        return self._map_study_dossier(doc)

    def get_study_dossiers_by_source_ids(self, source_ids: list[str]) -> list[StudyDossierDTO]:
        if not source_ids:
            return []
        try:
            db = self._provider.get_database()
            cursor = db["study_dossier"].find({"source_id": {"$in": list(set(source_ids))}}).sort("source_id", 1)
            docs = list(cursor)
        except PyMongoError as exc:
            raise DatabaseUnavailableError("mongodb", str(exc)) from exc
        return [self._map_study_dossier(doc) for doc in docs]

    def get_raw_source_record_by_external_id(
        self, source_id: str, external_record_id: str
    ) -> RawSourceReferenceDTO | None:
        try:
            db = self._provider.get_database()
            doc = db["raw_source_record"].find_one(
                {"source_id": source_id, "external_record_id": external_record_id}
            )
        except PyMongoError as exc:
            raise DatabaseUnavailableError("mongodb", str(exc)) from exc
        if not doc:
            return None

        license_meta = dict(doc.get("license_metadata") or {})
        permitted = bool(license_meta.get("redistribution_permitted", False))
        # Regla RNF-08: si la licencia no permite redistribucion del contenido, solo exponer referencia y checksum
        exposed_payload = dict(doc["raw_payload"]) if permitted and isinstance(doc.get("raw_payload"), dict) else None
        return RawSourceReferenceDTO(
            raw_document_id=str(doc["_id"]),
            import_batch_id=str(doc["import_batch_id"]),
            source_id=str(doc["source_id"]),
            external_record_id=str(doc["external_record_id"]),
            provider=str(doc["provider"]),
            checksum=str(doc["checksum"]),
            license_id=str(license_meta.get("license_id", "UNKNOWN")),
            redistribution_permitted=permitted,
            raw_payload=exposed_payload,
        )
