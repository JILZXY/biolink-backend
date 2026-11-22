// ============================================================================
// BioLink - Etapa 4: Diseno Fisico de Persistencia
// Archivo: 001_create_indexes.cypher
// Motor: Neo4j Community 2026.10.2
// Descripcion: Indices secundarios de busqueda y filtrado para grafo
// ============================================================================

// Indice sobre nombre cientifico aceptado para busquedas de punto de entrada
CREATE INDEX idx_taxon_accepted_name IF NOT EXISTS
FOR (t:Taxon) ON (t.accepted_name);

// Indice sobre grupo de organismo (ANIMAL, PLANT)
CREATE INDEX idx_taxon_organism_group IF NOT EXISTS
FOR (t:Taxon) ON (t.organism_group);

// Indice sobre tipo de interaccion en la afirmacion
CREATE INDEX idx_assertion_type_code IF NOT EXISTS
FOR (a:InteractionAssertion) ON (a.interaction_type_code);

// Indice compuesto sobre periodo temporal de la afirmacion
CREATE INDEX idx_assertion_period IF NOT EXISTS
FOR (a:InteractionAssertion) ON (a.period_start, a.period_end);

// Indice sobre metodo de captura de evidencia en la afirmacion
CREATE INDEX idx_assertion_method IF NOT EXISTS
FOR (a:InteractionAssertion) ON (a.method_code);

// Indice sobre estado de proteccion de la localidad
CREATE INDEX idx_locality_protected_status IF NOT EXISTS
FOR (l:Locality) ON (l.protected_status);
