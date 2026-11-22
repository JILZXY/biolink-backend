-- ============================================================================
-- BioLink - Etapa 4: Diseno Fisico de Persistencia
-- Archivo: 001_create_schema.sql
-- Motor: PostgreSQL 18.7 (Debian 18.7-1.pgdg13+5)
-- Descripcion: Esquema normalizado relacional para identidades, control
--              transaccional, trazabilidad cientifica y Outbox Pattern.
-- ============================================================================

-- Tabla de control de migraciones
CREATE TABLE IF NOT EXISTS schema_migrations (
    version VARCHAR(64) PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    description TEXT NOT NULL
);

-- ----------------------------------------------------------------------------
-- 1. Usuarios, Roles y Seguridad Administrativa (ACT-02, ACT-03)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS app_user (
    user_id VARCHAR(64) PRIMARY KEY,
    display_name VARCHAR(160) NOT NULL,
    email VARCHAR(255) NOT NULL UNIQUE,
    password_hash VARCHAR(255) NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT chk_app_user_email_nonempty CHECK (length(trim(email)) > 3),
    CONSTRAINT chk_app_user_name_nonempty CHECK (length(trim(display_name)) > 0)
);

CREATE TABLE IF NOT EXISTS role (
    role_id VARCHAR(64) PRIMARY KEY,
    code VARCHAR(64) NOT NULL UNIQUE,
    name VARCHAR(128) NOT NULL,
    description TEXT NOT NULL,
    CONSTRAINT chk_role_code_upper CHECK (code = upper(code))
);

CREATE TABLE IF NOT EXISTS user_role (
    user_id VARCHAR(64) NOT NULL REFERENCES app_user(user_id) ON DELETE CASCADE,
    role_id VARCHAR(64) NOT NULL REFERENCES role(role_id) ON DELETE RESTRICT,
    assigned_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (user_id, role_id)
);

CREATE TABLE IF NOT EXISTS user_session (
    session_id VARCHAR(128) PRIMARY KEY,
    user_id VARCHAR(64) NOT NULL REFERENCES app_user(user_id) ON DELETE CASCADE,
    token_hash VARCHAR(128) NOT NULL UNIQUE,
    ip_address INET,
    user_agent TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    expires_at TIMESTAMPTZ NOT NULL,
    revoked_at TIMESTAMPTZ,
    CONSTRAINT chk_session_expiry CHECK (expires_at > created_at)
);

-- ----------------------------------------------------------------------------
-- 2. Fuentes Bibliograficas y Procedencia (RD-02, RD-15)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS source (
    source_id VARCHAR(64) PRIMARY KEY,
    source_type VARCHAR(64) NOT NULL,
    title TEXT NOT NULL,
    authors TEXT NOT NULL,
    publication_year INTEGER NOT NULL,
    doi VARCHAR(255),
    uri TEXT,
    publisher VARCHAR(255),
    citation_text TEXT NOT NULL,
    license_id VARCHAR(64) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT chk_source_type CHECK (
        source_type IN ('PUBLICATION', 'THESIS', 'DATASET', 'REPOSITORY', 'OTHER')
    ),
    CONSTRAINT chk_source_year CHECK (publication_year BETWEEN 1753 AND 2100)
);

CREATE TABLE IF NOT EXISTS source_version (
    source_version_id VARCHAR(64) PRIMARY KEY,
    source_id VARCHAR(64) NOT NULL REFERENCES source(source_id) ON DELETE RESTRICT,
    provider_version VARCHAR(64) NOT NULL,
    release_date DATE,
    retrieved_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    endpoint_or_file TEXT NOT NULL,
    query_parameters JSONB NOT NULL DEFAULT '{}'::jsonb,
    filters JSONB NOT NULL DEFAULT '{}'::jsonb,
    license_id VARCHAR(64) NOT NULL,
    checksum VARCHAR(128) NOT NULL,
    CONSTRAINT chk_source_version_checksum CHECK (length(trim(checksum)) >= 32)
);

-- ----------------------------------------------------------------------------
-- 3. Contexto Geografico y Espacial (RD-07, RD-08)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS locality (
    locality_id VARCHAR(64) PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    locality_type VARCHAR(64) NOT NULL,
    parent_locality_id VARCHAR(64) REFERENCES locality(locality_id) ON DELETE RESTRICT,
    protected_status VARCHAR(64) NOT NULL,
    spatial_validation_status VARCHAR(64) NOT NULL,
    geometry_reference TEXT,
    coordinate_precision VARCHAR(64),
    source_id VARCHAR(64) NOT NULL REFERENCES source(source_id) ON DELETE RESTRICT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT chk_locality_type CHECK (
        locality_type IN ('PROTECTED_AREA', 'COMMUNITY_RESERVE', 'EJIDO', 'SAMPLING_SITE', 'FIELD_STATION', 'STUDY_AREA', 'OTHER')
    ),
    CONSTRAINT chk_locality_protected_status CHECK (
        protected_status IN ('INSIDE_ANP', 'OUTSIDE_ANP', 'ADJACENT', 'UNDETERMINED', 'NOT_REPORTED', 'PROTECTED_ANP', 'EXTERNAL_EJIDO')
    ),
    CONSTRAINT chk_locality_spatial_status CHECK (
        spatial_validation_status IN ('VERIFIED_POLYGON', 'POINT_COORDINATES', 'DESCRIPTIVE_ONLY', 'PENDING_VALIDATION', 'INVALID', 'INSIDE_ANP', 'OUTSIDE_ANP')
    )
);

-- ----------------------------------------------------------------------------
-- 4. Taxonomia y Nombres Cientificos (RD-10, RD-11, RD-14)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS taxon (
    taxon_id VARCHAR(64) PRIMARY KEY,
    accepted_name VARCHAR(255) NOT NULL,
    scientific_authorship VARCHAR(160) NOT NULL,
    taxonomic_rank VARCHAR(32) NOT NULL,
    organism_group VARCHAR(32) NOT NULL,
    taxonomic_status VARCHAR(64) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT chk_taxon_rank CHECK (
        taxonomic_rank IN ('SPECIES', 'GENUS', 'FAMILY', 'ORDER', 'HIGHER_TAXON', 'MORPHOSPECIES', 'UNRESOLVED')
    ),
    CONSTRAINT chk_taxon_group CHECK (
        organism_group IN ('ANIMAL', 'PLANT', 'FUNGI', 'OTHER')
    ),
    CONSTRAINT chk_taxon_status CHECK (
        taxonomic_status IN ('ACCEPTED', 'SYNONYM', 'DOUBTFUL', 'UNRESOLVED', 'UNRESOLVED_TO_SPECIES', 'NEEDS_RECONCILIATION')
    )
);

CREATE TABLE IF NOT EXISTS taxon_name (
    taxon_name_id VARCHAR(64) PRIMARY KEY,
    taxon_id VARCHAR(64) NOT NULL REFERENCES taxon(taxon_id) ON DELETE RESTRICT,
    name VARCHAR(255) NOT NULL,
    name_usage VARCHAR(32) NOT NULL,
    language VARCHAR(16) NOT NULL DEFAULT 'la',
    source_id VARCHAR(64) NOT NULL REFERENCES source(source_id) ON DELETE RESTRICT,
    is_preferred BOOLEAN NOT NULL DEFAULT false,
    verbatim_name VARCHAR(255),
    CONSTRAINT chk_taxon_name_usage CHECK (
        name_usage IN ('ACCEPTED', 'SYNONYM', 'COMMON_NAME', 'VERBATIM_ORIGINAL', 'BASIONYM')
    )
);

CREATE TABLE IF NOT EXISTS taxon_identifier (
    taxon_identifier_id VARCHAR(64) PRIMARY KEY,
    taxon_id VARCHAR(64) NOT NULL REFERENCES taxon(taxon_id) ON DELETE RESTRICT,
    scheme VARCHAR(32) NOT NULL,
    external_id VARCHAR(128) NOT NULL,
    retrieved_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    source_id VARCHAR(64) NOT NULL REFERENCES source(source_id) ON DELETE RESTRICT,
    CONSTRAINT uq_taxon_identifier_scheme_id UNIQUE (scheme, external_id),
    CONSTRAINT chk_taxon_identifier_scheme CHECK (
        scheme IN ('GBIF', 'ENCICLOVIDA', 'NCBI', 'COL', 'IPNI', 'ITIS', 'CUSTOM')
    )
);

-- ----------------------------------------------------------------------------
-- 5. Evidencias, Ocurrencias y Tipo de Interaccion (RD-01, RD-03, RD-05)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS interaction_type (
    interaction_type_id VARCHAR(64) PRIMARY KEY,
    code VARCHAR(64) NOT NULL UNIQUE,
    label VARCHAR(128) NOT NULL,
    definition TEXT NOT NULL,
    actor_role VARCHAR(64) NOT NULL,
    target_role VARCHAR(64) NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT true,
    CONSTRAINT chk_interaction_type_code CHECK (
        code IN ('CONSUMES_FRUIT_OF', 'REMOVES_FRUIT_OF', 'CONSUMES_OR_REMOVES_FRUIT_OF', 'DISPERSES_SEEDS_OF')
    ),
    CONSTRAINT chk_interaction_actor_role CHECK (actor_role IN ('ANIMAL', 'HERBIVORE', 'FRUGIVORE', 'OTHER')),
    CONSTRAINT chk_interaction_target_role CHECK (target_role IN ('PLANT', 'FRUIT', 'SEED', 'OTHER'))
);

CREATE TABLE IF NOT EXISTS evidence (
    evidence_id VARCHAR(64) PRIMARY KEY,
    source_id VARCHAR(64) NOT NULL REFERENCES source(source_id) ON DELETE RESTRICT,
    source_version_id VARCHAR(64) REFERENCES source_version(source_version_id) ON DELETE RESTRICT,
    source_locator VARCHAR(255) NOT NULL,
    evidence_kind VARCHAR(64) NOT NULL,
    method_code VARCHAR(64) NOT NULL,
    reported_term VARCHAR(128) NOT NULL,
    summary TEXT NOT NULL,
    interpretation_limit TEXT NOT NULL,
    period_start VARCHAR(32) NOT NULL,
    period_end VARCHAR(32) NOT NULL,
    license_id VARCHAR(64) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT chk_evidence_kind CHECK (
        evidence_kind IN ('SPECIMEN_SAMPLE', 'OBSERVATION', 'RECORDING', 'DATASET_ENTRY', 'LITERATURE_CITATION')
    ),
    CONSTRAINT chk_evidence_method CHECK (
        method_code IN ('FECAL_SEED_IDENTIFICATION', 'CAMERA_TRAP', 'FIELD_OBSERVATION', 'DATASET_RECORD', 'LITERATURE_REPORT', 'OTHER_REVIEWED')
    ),
    CONSTRAINT chk_evidence_period CHECK (period_end >= period_start)
);

CREATE TABLE IF NOT EXISTS occurrence (
    occurrence_id VARCHAR(64) PRIMARY KEY,
    taxon_id VARCHAR(64) NOT NULL REFERENCES taxon(taxon_id) ON DELETE RESTRICT,
    source_id VARCHAR(64) NOT NULL REFERENCES source(source_id) ON DELETE RESTRICT,
    source_record_id VARCHAR(128),
    locality_id VARCHAR(64) REFERENCES locality(locality_id) ON DELETE RESTRICT,
    event_date VARCHAR(32),
    latitude NUMERIC(9, 6),
    longitude NUMERIC(9, 6),
    coordinate_uncertainty_meters NUMERIC(10, 2),
    basis_of_record VARCHAR(64) NOT NULL,
    georeference_status VARCHAR(64) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT chk_occurrence_lat CHECK (latitude IS NULL OR (latitude BETWEEN -90.0 AND 90.0)),
    CONSTRAINT chk_occurrence_lon CHECK (longitude IS NULL OR (longitude BETWEEN -180.0 AND 180.0)),
    CONSTRAINT chk_occurrence_georef CHECK (
        georeference_status IN ('GEOREFERENCED', 'APPROXIMATE', 'UNVERIFIED', 'NOT_GEOREFERENCED')
    )
);

-- ----------------------------------------------------------------------------
-- 6. Afirmaciones de Interaccion y Revision (RD-03, RD-12, RD-13, RD-16)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS interaction_assertion (
    assertion_id VARCHAR(64) PRIMARY KEY,
    actor_taxon_id VARCHAR(64) NOT NULL REFERENCES taxon(taxon_id) ON DELETE RESTRICT,
    target_taxon_id VARCHAR(64) NOT NULL REFERENCES taxon(taxon_id) ON DELETE RESTRICT,
    interaction_type_id VARCHAR(64) NOT NULL REFERENCES interaction_type(interaction_type_id) ON DELETE RESTRICT,
    locality_id VARCHAR(64) NOT NULL REFERENCES locality(locality_id) ON DELETE RESTRICT,
    period_start VARCHAR(32) NOT NULL,
    period_end VARCHAR(32) NOT NULL,
    season_label VARCHAR(128) NOT NULL,
    method_code VARCHAR(64) NOT NULL,
    assertion_scope VARCHAR(64) NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'DRAFT',
    created_by VARCHAR(64) NOT NULL REFERENCES app_user(user_id) ON DELETE RESTRICT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    reviewed_by VARCHAR(64) REFERENCES app_user(user_id) ON DELETE RESTRICT,
    reviewed_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT chk_assertion_taxa_distinct CHECK (actor_taxon_id != target_taxon_id),
    CONSTRAINT chk_assertion_period CHECK (period_end >= period_start),
    CONSTRAINT chk_assertion_status CHECK (
        status IN ('DRAFT', 'IN_REVIEW', 'PUBLISHED', 'REJECTED', 'WITHDRAWN')
    ),
    CONSTRAINT chk_assertion_method CHECK (
        method_code IN ('FECAL_SEED_IDENTIFICATION', 'CAMERA_TRAP', 'FIELD_OBSERVATION', 'DATASET_RECORD', 'LITERATURE_REPORT', 'OTHER_REVIEWED')
    ),
    CONSTRAINT chk_assertion_review_consistency CHECK (
        (status = 'PUBLISHED' AND reviewed_by IS NOT NULL AND reviewed_at IS NOT NULL) OR
        (status != 'PUBLISHED')
    )
);

CREATE TABLE IF NOT EXISTS assertion_evidence (
    assertion_id VARCHAR(64) NOT NULL REFERENCES interaction_assertion(assertion_id) ON DELETE CASCADE,
    evidence_id VARCHAR(64) NOT NULL REFERENCES evidence(evidence_id) ON DELETE RESTRICT,
    evidence_role VARCHAR(32) NOT NULL DEFAULT 'PRIMARY',
    review_note VARCHAR(512),
    assigned_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (assertion_id, evidence_id),
    CONSTRAINT chk_assertion_evidence_role CHECK (evidence_role IN ('PRIMARY', 'SUPPORTING', 'CONTEXTUAL'))
);

CREATE TABLE IF NOT EXISTS review_event (
    review_event_id VARCHAR(64) PRIMARY KEY,
    assertion_id VARCHAR(64) NOT NULL REFERENCES interaction_assertion(assertion_id) ON DELETE RESTRICT,
    user_id VARCHAR(64) NOT NULL REFERENCES app_user(user_id) ON DELETE RESTRICT,
    previous_status VARCHAR(32) NOT NULL,
    new_status VARCHAR(32) NOT NULL,
    reason TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT chk_review_event_transition CHECK (previous_status != new_status),
    CONSTRAINT chk_review_event_status_vals CHECK (
        previous_status IN ('DRAFT', 'IN_REVIEW', 'PUBLISHED', 'REJECTED', 'WITHDRAWN') AND
        new_status IN ('DRAFT', 'IN_REVIEW', 'PUBLISHED', 'REJECTED', 'WITHDRAWN')
    )
);

-- ----------------------------------------------------------------------------
-- 7. Ingestion de Lotes y Registros Crudos (RNF-03, RNF-04)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS import_batch (
    import_batch_id VARCHAR(64) PRIMARY KEY,
    source_id VARCHAR(64) NOT NULL REFERENCES source(source_id) ON DELETE RESTRICT,
    source_version_id VARCHAR(64) REFERENCES source_version(source_version_id) ON DELETE RESTRICT,
    started_by VARCHAR(64) NOT NULL REFERENCES app_user(user_id) ON DELETE RESTRICT,
    started_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    finished_at TIMESTAMPTZ,
    query_parameters JSONB NOT NULL DEFAULT '{}'::jsonb,
    filters JSONB NOT NULL DEFAULT '{}'::jsonb,
    status VARCHAR(32) NOT NULL DEFAULT 'RUNNING',
    received_count INTEGER NOT NULL DEFAULT 0,
    accepted_count INTEGER NOT NULL DEFAULT 0,
    rejected_count INTEGER NOT NULL DEFAULT 0,
    CONSTRAINT chk_import_batch_status CHECK (
        status IN ('RUNNING', 'COMPLETED', 'FAILED', 'PARTIAL')
    ),
    CONSTRAINT chk_import_batch_counts CHECK (
        received_count >= 0 AND accepted_count >= 0 AND rejected_count >= 0 AND
        (accepted_count + rejected_count <= received_count)
    )
);

CREATE TABLE IF NOT EXISTS import_record (
    import_record_id VARCHAR(64) PRIMARY KEY,
    import_batch_id VARCHAR(64) NOT NULL REFERENCES import_batch(import_batch_id) ON DELETE CASCADE,
    external_record_id VARCHAR(128) NOT NULL,
    raw_document_id VARCHAR(128),
    validation_status VARCHAR(32) NOT NULL DEFAULT 'PENDING',
    canonical_taxon_id VARCHAR(64) REFERENCES taxon(taxon_id) ON DELETE SET NULL,
    validation_notes TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT chk_import_record_status CHECK (
        validation_status IN ('PENDING', 'VALID', 'INVALID', 'UNRESOLVED', 'DUPLICATE')
    )
);

-- ----------------------------------------------------------------------------
-- 8. Transactional Outbox Pattern y Auditoria Tecnica
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS outbox_event (
    outbox_event_id VARCHAR(64) PRIMARY KEY,
    aggregate_type VARCHAR(64) NOT NULL,
    aggregate_id VARCHAR(64) NOT NULL,
    event_type VARCHAR(64) NOT NULL,
    payload_json JSONB NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'PENDING',
    attempts INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    processed_at TIMESTAMPTZ,
    error_message TEXT,
    CONSTRAINT chk_outbox_event_status CHECK (
        status IN ('PENDING', 'PROCESSING', 'PROCESSED', 'FAILED', 'DEAD_LETTER')
    ),
    CONSTRAINT chk_outbox_attempts CHECK (attempts >= 0)
);

CREATE TABLE IF NOT EXISTS audit_log (
    audit_id BIGSERIAL PRIMARY KEY,
    entity_type VARCHAR(64) NOT NULL,
    entity_id VARCHAR(64) NOT NULL,
    action VARCHAR(32) NOT NULL,
    performed_by VARCHAR(64) REFERENCES app_user(user_id) ON DELETE SET NULL,
    performed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    details JSONB NOT NULL DEFAULT '{}'::jsonb,
    CONSTRAINT chk_audit_action CHECK (
        action IN ('INSERT', 'UPDATE', 'DELETE', 'LOGIN', 'LOGOUT', 'SECURITY')
    )
);

-- Registrar migracion inicial completada
INSERT INTO schema_migrations (version, description)
VALUES ('001_create_schema', 'Creacion inicial de 20 tablas relacionales, constraints y outbox')
ON CONFLICT (version) DO NOTHING;
