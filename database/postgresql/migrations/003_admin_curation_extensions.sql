-- ============================================================================
-- BioLink - Etapa 5: Extensiones Administrativas, Curaduria y Trazabilidad Outbox
-- Archivo: 003_admin_curation_extensions.sql
-- Motor: PostgreSQL 18.7 (Debian 18.7-1.pgdg13+5)
-- Descripcion: Extensiones idempotentes para OP-09..OP-14, versionado de fichas
--              y trazabilidad de proyeccion hacia Neo4j y MongoDB.
-- ============================================================================

-- 1. Metadatos de lote e ingestión normalizada (OP-09, OP-10)
ALTER TABLE import_batch
    ADD COLUMN IF NOT EXISTS endpoint_or_file TEXT,
    ADD COLUMN IF NOT EXISTS provider_version VARCHAR(64);

ALTER TABLE import_record
    ADD COLUMN IF NOT EXISTS verbatim_name VARCHAR(255),
    ADD COLUMN IF NOT EXISTS proposed_external_scheme VARCHAR(32),
    ADD COLUMN IF NOT EXISTS proposed_external_id VARCHAR(128),
    ADD COLUMN IF NOT EXISTS matching_rule VARCHAR(128),
    ADD COLUMN IF NOT EXISTS normalized_by VARCHAR(64) REFERENCES app_user(user_id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS normalized_at TIMESTAMPTZ;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'chk_import_record_ext_scheme'
    ) THEN
        ALTER TABLE import_record
            ADD CONSTRAINT chk_import_record_ext_scheme CHECK (
                proposed_external_scheme IS NULL OR
                proposed_external_scheme IN ('GBIF', 'ENCICLOVIDA', 'NCBI', 'COL', 'IPNI', 'ITIS', 'CUSTOM')
            );
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_import_record_pending_or_unresolved
ON import_record (import_batch_id, created_at ASC)
WHERE validation_status IN ('PENDING', 'UNRESOLVED');

-- 2. Control de versionado de agregado y estado honesto de proyeccion (OP-11, OP-12)
ALTER TABLE interaction_assertion
    ADD COLUMN IF NOT EXISTS aggregate_version INTEGER NOT NULL DEFAULT 1,
    ADD COLUMN IF NOT EXISTS projection_status VARCHAR(32) NOT NULL DEFAULT 'NOT_APPLICABLE',
    ADD COLUMN IF NOT EXISTS last_projected_at TIMESTAMPTZ;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'chk_assertion_projection_status'
    ) THEN
        ALTER TABLE interaction_assertion
            ADD CONSTRAINT chk_assertion_projection_status CHECK (
                projection_status IN ('NOT_APPLICABLE', 'PENDING', 'SYNCED', 'FAILED')
            );
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'chk_assertion_aggregate_version'
    ) THEN
        ALTER TABLE interaction_assertion
            ADD CONSTRAINT chk_assertion_aggregate_version CHECK (aggregate_version >= 1);
    END IF;
END $$;

-- 3. Columnas explicitas de versionado en Outbox (OP-12)
ALTER TABLE outbox_event
    ADD COLUMN IF NOT EXISTS aggregate_version INTEGER NOT NULL DEFAULT 1,
    ADD COLUMN IF NOT EXISTS payload_version INTEGER NOT NULL DEFAULT 1;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'chk_outbox_versions'
    ) THEN
        ALTER TABLE outbox_event
            ADD CONSTRAINT chk_outbox_versions CHECK (
                aggregate_version >= 1 AND payload_version >= 1
            );
    END IF;
END $$;

DROP INDEX IF EXISTS idx_outbox_pending_polling;
CREATE INDEX IF NOT EXISTS idx_outbox_pending_polling
ON outbox_event (created_at ASC)
WHERE status IN ('PENDING', 'FAILED');

-- 4. Control de reconciliacion de expediente documental por taxon (OP-13)
ALTER TABLE taxon
    ADD COLUMN IF NOT EXISTS dossier_version INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS dossier_sync_status VARCHAR(32) NOT NULL DEFAULT 'SYNCED';

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'chk_taxon_dossier_version'
    ) THEN
        ALTER TABLE taxon
            ADD CONSTRAINT chk_taxon_dossier_version CHECK (dossier_version >= 0);
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'chk_taxon_dossier_sync_status'
    ) THEN
        ALTER TABLE taxon
            ADD CONSTRAINT chk_taxon_dossier_sync_status CHECK (
                dossier_sync_status IN ('SYNCED', 'PENDING_RECONCILIATION', 'ERROR')
            );
    END IF;
END $$;

INSERT INTO schema_migrations (version, description)
VALUES ('003_admin_curation_extensions', 'Columnas administrativas para OP-09..OP-14, versionado Outbox y reconciliacion de fichas')
ON CONFLICT (version) DO NOTHING;
