// ============================================================================
// BioLink - Etapa 4: Diseno Fisico de Persistencia
// Archivo: withdraw_assertion.cypher
// Motor: Neo4j Community 2026.10.2
// Descripcion: Retiro de afirmaciones no vigentes (WITHDRAWN, REJECTED, DELETED)
//              REGLA ESTRICTA: El grafo no retiene afirmaciones no publicadas.
// ============================================================================

// 1. Retiro individual puntual (invocado por evento ASSERTION_WITHDRAWN)
// Parametro: $assertion_id
MATCH (a:InteractionAssertion {assertion_id: $assertion_id})
DETACH DELETE a;

// 2. Retiro masivo controlado por lotes (Evita MemoryPoolOutOfMemoryError en 50k)
// Parametro: $batch_assertion_ids (lista de IDs a retirar)
// NOTA TECNICA: El procesamiento por transacciones de 1000 filas mantiene
// el consumo de memoria dentro del limite transaccional (268.8 MiB).
/*
UNWIND $batch_assertion_ids AS aid
CALL {
  WITH aid
  MATCH (a:InteractionAssertion {assertion_id: aid})
  DETACH DELETE a
} IN TRANSACTIONS OF 1000 ROWS;
*/
