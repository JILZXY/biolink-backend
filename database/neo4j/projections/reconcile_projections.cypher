// ============================================================================
// BioLink - Etapa 4: Diseno Fisico de Persistencia
// Archivo: reconcile_projections.cypher
// Motor: Neo4j Community 2026.10.2
// Descripcion: Consultas de auditoria y reconciliacion de estado entre
//              PostgreSQL (Ground Truth) y la proyeccion de Grafo Neo4j.
// ============================================================================

// 1. Auditoria de conteo de entidades proyectadas
MATCH (t:Taxon) WITH count(t) AS total_taxa
MATCH (a:InteractionAssertion) WITH total_taxa, count(a) AS total_assertions
MATCH (l:Locality) WITH total_taxa, total_assertions, count(l) AS total_localities
MATCH (e:Evidence) WITH total_taxa, total_assertions, total_localities, count(e) AS total_evidences
MATCH (s:Source) WITH total_taxa, total_assertions, total_localities, total_evidences, count(s) AS total_sources
RETURN total_taxa, total_assertions, total_localities, total_evidences, total_sources;

// 2. Deteccion de afirmaciones que carecen de Taxon Actor o Objetivo (Integridad Estructural)
MATCH (a:InteractionAssertion)
WHERE NOT ( (:Taxon)-[:ACTOR_IN]->(a) AND (a)-[:TARGETS]->(:Taxon) )
RETURN a.assertion_id AS dangling_assertion_id, a.status AS status;

// 3. Deteccion de afirmaciones que carecen de Evidencia (Violacion de RD-13)
MATCH (a:InteractionAssertion)
WHERE NOT ( (a)-[:SUPPORTED_BY]->(:Evidence) )
RETURN a.assertion_id AS assertion_without_evidence;

// 4. Auditoria de afirmaciones huerfanas contra lista de IDs validos de PostgreSQL
// Parametro: $valid_published_ids (conjunto de assertion_ids en status PUBLISHED en Postgres)
/*
MATCH (a:InteractionAssertion)
WHERE NOT a.assertion_id IN $valid_published_ids
RETURN a.assertion_id AS stale_graph_assertion_id;
*/
