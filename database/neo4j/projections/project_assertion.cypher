// ============================================================================
// BioLink - Etapa 4: Diseno Fisico de Persistencia
// Archivo: project_assertion.cypher
// Motor: Neo4j Community 2026.10.2
// Descripcion: Proyeccion idempotente de afirmacion aprobada desde Outbox Event.
//              REGLA ESTRICTA: Solo proyecta afirmaciones en status = 'PUBLISHED'.
// ============================================================================

// Parametros esperados:
// $assertion_id, $actor_taxon_id, $target_taxon_id, $interaction_type_code,
// $locality_id, $period_start, $period_end, $season_label, $method_code,
// $assertion_scope, $status, $evidence_id, $evidence_kind, $source_locator,
// $source_id, $source_type, $source_title, $publication_year, $actor_name, $target_name

WITH $status AS st
WHERE st = 'PUBLISHED'

// 1. Asegurar existencia de nodos de entidad canonica
MERGE (actor:Taxon {taxon_id: $actor_taxon_id})
ON CREATE SET
  actor.accepted_name = coalesce($actor_name, 'Unknown Taxon'),
  actor.organism_group = 'ANIMAL',
  actor.projected_at = datetime()

MERGE (target:Taxon {taxon_id: $target_taxon_id})
ON CREATE SET
  target.accepted_name = coalesce($target_name, 'Unknown Taxon'),
  target.organism_group = 'PLANT',
  target.projected_at = datetime()

MERGE (loc:Locality {locality_id: $locality_id})
ON CREATE SET
  loc.projected_at = datetime()

MERGE (src:Source {source_id: $source_id})
ON CREATE SET
  src.source_type = coalesce($source_type, 'PUBLICATION'),
  src.title = coalesce($source_title, 'Scientific Publication'),
  src.publication_year = coalesce($publication_year, 2000),
  src.projected_at = datetime()

MERGE (ev:Evidence {evidence_id: $evidence_id})
ON CREATE SET
  ev.evidence_kind = coalesce($evidence_kind, 'OBSERVATION'),
  ev.method_code = $method_code,
  ev.source_locator = coalesce($source_locator, 'Reference Citation'),
  ev.review_status = 'VERIFIED',
  ev.projected_at = datetime()

// 2. Proyectar el nodo de Afirmacion (identidad 1:1 con assertion_id relacional)
MERGE (assertion:InteractionAssertion {assertion_id: $assertion_id})
ON CREATE SET
  assertion.interaction_type_code = $interaction_type_code,
  assertion.period_start = $period_start,
  assertion.period_end = $period_end,
  assertion.season_label = $season_label,
  assertion.method_code = $method_code,
  assertion.status = 'PUBLISHED',
  assertion.assertion_scope = coalesce($assertion_scope, 'REFERENCE_FIXTURE'),
  assertion.projected_at = datetime()
ON MATCH SET
  assertion.interaction_type_code = $interaction_type_code,
  assertion.period_start = $period_start,
  assertion.period_end = $period_end,
  assertion.season_label = $season_label,
  assertion.method_code = $method_code,
  assertion.status = 'PUBLISHED',
  assertion.updated_at = datetime()

// 3. Proyectar relaciones semanticas obligatorias
MERGE (actor)-[:ACTOR_IN]->(assertion)
MERGE (assertion)-[:TARGETS]->(target)
MERGE (assertion)-[:OBSERVED_AT]->(loc)
MERGE (assertion)-[:SUPPORTED_BY]->(ev)
MERGE (ev)-[:CITED_FROM]->(src)

RETURN assertion.assertion_id AS projected_assertion_id, assertion.status AS status;
