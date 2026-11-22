-- ============================================================================
-- BioLink - Etapa 4: Diseno Fisico de Persistencia
-- Archivo: 002_create_indexes.sql
-- Motor: PostgreSQL 18.7 (Debian 18.7-1.pgdg13+5)
-- Descripcion: Estrategia de indexacion optimizada para filtros reales,
--              resolucion de la degradacion en R2 (50k) y Outbox polling.
-- ============================================================================

-- ----------------------------------------------------------------------------
-- 1. Optimizacion de Busqueda Taxonomica (OP-01 / Workload R1)
-- ----------------------------------------------------------------------------
-- Busqueda rapida de nombres aceptados y sinonimos insensible a mayusculas
CREATE INDEX IF NOT EXISTS idx_taxon_name_lower_lookup 
ON taxon_name (lower(name), taxon_id);

CREATE INDEX IF NOT EXISTS idx_taxon_name_taxon_fk
ON taxon_name (taxon_id);

CREATE INDEX IF NOT EXISTS idx_taxon_accepted_name
ON taxon (accepted_name);

CREATE INDEX IF NOT EXISTS idx_taxon_identifier_lookup
ON taxon_identifier (scheme, external_id, taxon_id);

CREATE INDEX IF NOT EXISTS idx_taxon_identifier_taxon_fk
ON taxon_identifier (taxon_id);

-- ----------------------------------------------------------------------------
-- 2. Optimizacion Critica para Afirmaciones Filtradas (OP-03, OP-05 / Workload R2)
--    Resuelve la degradacion de latencia observada a 50,000 afirmaciones.
-- ----------------------------------------------------------------------------
-- Indice Parcial y Cubriente (Covering Partial Index):
-- - Filtra solo status = 'PUBLISHED' (descarta borradores/rechazados del arbol B).
-- - Llaves B-Tree: (locality_id, period_start, assertion_id) para resolver
--   igualdad de localidad, rango temporal y ordenamiento sin Sort en memoria.
-- - Clausula INCLUDE: provee las columnas de proyeccion para Index Only Scan.
CREATE INDEX IF NOT EXISTS idx_assertion_published_r2_covering
ON interaction_assertion (locality_id, period_start, assertion_id)
INCLUDE (actor_taxon_id, target_taxon_id, interaction_type_id, method_code, period_end)
WHERE status = 'PUBLISHED';

-- Indices parciales especializados para recorridos relacionales de vecindario (OP-03)
CREATE INDEX IF NOT EXISTS idx_assertion_actor_published
ON interaction_assertion (actor_taxon_id, target_taxon_id, locality_id)
WHERE status = 'PUBLISHED';

CREATE INDEX IF NOT EXISTS idx_assertion_target_published
ON interaction_assertion (target_taxon_id, actor_taxon_id, locality_id)
WHERE status = 'PUBLISHED';

-- ----------------------------------------------------------------------------
-- 3. Claves Foraneas e Integridad Referencial
-- ----------------------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_locality_parent
ON locality (parent_locality_id);

CREATE INDEX IF NOT EXISTS idx_locality_source
ON locality (source_id);

CREATE INDEX IF NOT EXISTS idx_source_version_source
ON source_version (source_id);

CREATE INDEX IF NOT EXISTS idx_evidence_source
ON evidence (source_id);

CREATE INDEX IF NOT EXISTS idx_evidence_source_version
ON evidence (source_version_id);

CREATE INDEX IF NOT EXISTS idx_occurrence_taxon_loc
ON occurrence (taxon_id, locality_id);

CREATE INDEX IF NOT EXISTS idx_occurrence_source
ON occurrence (source_id);

CREATE INDEX IF NOT EXISTS idx_assertion_evidence_evidence_fk
ON assertion_evidence (evidence_id);

CREATE INDEX IF NOT EXISTS idx_review_event_assertion
ON review_event (assertion_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_import_record_batch_status
ON import_record (import_batch_id, validation_status);

CREATE INDEX IF NOT EXISTS idx_import_record_canonical_taxon
ON import_record (canonical_taxon_id);

CREATE INDEX IF NOT EXISTS idx_user_role_role
ON user_role (role_id);

CREATE INDEX IF NOT EXISTS idx_session_active
ON user_session (token_hash)
WHERE revoked_at IS NULL;

-- ----------------------------------------------------------------------------
-- 4. Transactional Outbox Pattern y Auditoria (Alta Eficiencia O(1))
-- ----------------------------------------------------------------------------
-- Indice parcial exclusivo para eventos PENDING: permite polling instantaneo
-- sin escanear el historial de eventos ya procesados.
CREATE INDEX IF NOT EXISTS idx_outbox_pending_polling
ON outbox_event (created_at ASC)
WHERE status = 'PENDING';

CREATE INDEX IF NOT EXISTS idx_outbox_aggregate_lookup
ON outbox_event (aggregate_type, aggregate_id);

CREATE INDEX IF NOT EXISTS idx_audit_log_entity
ON audit_log (entity_type, entity_id, performed_at DESC);

-- Registrar migracion de indices completada
INSERT INTO schema_migrations (version, description)
VALUES ('002_create_indexes', 'Creacion de indices de optimizacion, R2 covering parcial y outbox polling')
ON CONFLICT (version) DO NOTHING;
