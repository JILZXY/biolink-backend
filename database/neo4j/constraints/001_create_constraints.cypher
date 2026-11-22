// ============================================================================
// BioLink - Etapa 4: Diseno Fisico de Persistencia
// Archivo: 001_create_constraints.cypher
// Motor: Neo4j Community 2026.10.2
// Descripcion: Restricciones de unicidad para identidades canonicas
// ============================================================================

// Unicidad obligatoria de Taxon (mapeado a taxon.taxon_id de PostgreSQL)
CREATE CONSTRAINT uq_taxon_id IF NOT EXISTS
FOR (t:Taxon) REQUIRE t.taxon_id IS UNIQUE;

// Unicidad obligatoria de Afirmacion (mapeado a interaction_assertion.assertion_id)
CREATE CONSTRAINT uq_assertion_id IF NOT EXISTS
FOR (a:InteractionAssertion) REQUIRE a.assertion_id IS UNIQUE;

// Unicidad obligatoria de Localidad (mapeado a locality.locality_id)
CREATE CONSTRAINT uq_locality_id IF NOT EXISTS
FOR (l:Locality) REQUIRE l.locality_id IS UNIQUE;

// Unicidad obligatoria de Evidencia (mapeado a evidence.evidence_id)
CREATE CONSTRAINT uq_evidence_id IF NOT EXISTS
FOR (e:Evidence) REQUIRE e.evidence_id IS UNIQUE;

// Unicidad obligatoria de Fuente (mapeado a source.source_id)
CREATE CONSTRAINT uq_source_id IF NOT EXISTS
FOR (s:Source) REQUIRE s.source_id IS UNIQUE;
