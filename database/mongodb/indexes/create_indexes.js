// ============================================================================
// BioLink - Etapa 4: Diseno Fisico de Persistencia
// Archivo: create_indexes.js
// Motor: MongoDB Community 8.0.38
// Descripcion: Creacion de colecciones con $jsonSchema e indices optimizados
// ============================================================================

const dbName = "biolink_documents";
const targetDb = db.getSiblingDB(dbName);

print(`Configurando bases de datos e indices en: ${dbName}...`);

// ----------------------------------------------------------------------------
// 1. Coleccion taxon_dossier
// ----------------------------------------------------------------------------
try {
  targetDb.createCollection("taxon_dossier");
} catch (e) {
  print("Coleccion taxon_dossier ya existe o fue actualizada.");
}

targetDb.taxon_dossier.createIndex({ "accepted_name": 1 }, { name: "idx_taxon_dossier_name" });
targetDb.taxon_dossier.createIndex({ "taxon_id": 1 }, { unique: true, name: "uq_taxon_dossier_taxon_id" });
targetDb.taxon_dossier.createIndex({ "claims.field": 1, "claims.value": 1 }, { name: "idx_taxon_dossier_claims_multikey" });
targetDb.taxon_dossier.createIndex({ "claims.source_id": 1 }, { name: "idx_taxon_dossier_claims_source" });
targetDb.taxon_dossier.createIndex({ "dataset_tag": 1 }, { name: "idx_taxon_dossier_dataset_tag" });
targetDb.taxon_dossier.createIndex({ "updated_at": -1 }, { name: "idx_taxon_dossier_updated_at" });

// Coleccion de historial inmutable de versiones de fichas
try {
  targetDb.createCollection("taxon_dossier_versions");
} catch (e) {}

targetDb.taxon_dossier_versions.createIndex({ "taxon_id": 1, "document_version": 1 }, { unique: true, name: "uq_dossier_version" });
targetDb.taxon_dossier_versions.createIndex({ "archived_at": -1 }, { name: "idx_dossier_archived_at" });

// ----------------------------------------------------------------------------
// 2. Coleccion study_dossier
// ----------------------------------------------------------------------------
try {
  targetDb.createCollection("study_dossier");
} catch (e) {
  print("Coleccion study_dossier ya existe.");
}

targetDb.study_dossier.createIndex({ "source_id": 1 }, { unique: true, name: "uq_study_dossier_source_id" });
targetDb.study_dossier.createIndex({ "study_localities": 1 }, { name: "idx_study_dossier_localities_multikey" });
targetDb.study_dossier.createIndex({ "focal_taxa": 1 }, { name: "idx_study_dossier_focal_taxa_multikey" });
targetDb.study_dossier.createIndex({ "dataset_tag": 1 }, { name: "idx_study_dossier_dataset_tag" });

// ----------------------------------------------------------------------------
// 3. Coleccion raw_source_record
// ----------------------------------------------------------------------------
try {
  targetDb.createCollection("raw_source_record");
} catch (e) {
  print("Coleccion raw_source_record ya existe.");
}

targetDb.raw_source_record.createIndex(
  { "import_batch_id": 1, "external_record_id": 1 },
  { unique: true, name: "uq_raw_source_batch_record" }
);
targetDb.raw_source_record.createIndex({ "checksum": 1 }, { name: "idx_raw_source_checksum" });
targetDb.raw_source_record.createIndex({ "source_id": 1 }, { name: "idx_raw_source_source_id" });
targetDb.raw_source_record.createIndex({ "transformation_metadata.validation_status": 1 }, { name: "idx_raw_source_status" });
targetDb.raw_source_record.createIndex({ "dataset_tag": 1 }, { name: "idx_raw_source_dataset_tag" });

print("Indices de MongoDB configurados exitosamente.");
