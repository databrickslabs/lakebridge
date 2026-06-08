CREATE TABLE IF NOT EXISTS details (
    recon_table_id   BIGINT          NOT NULL COMMENT 'Join key to main.',
    recon_type       STRING          NOT NULL COMMENT 'mismatch | missing_in_source | missing_in_target | threshold_mismatch.',
    record_key       VARIANT COMMENT 'Join-column values identifying the sampled record.',
    source_row       VARIANT COMMENT 'Source-side image; null for missing_in_source.',
    target_row       VARIANT COMMENT 'Target-side image; null for missing_in_target.',
    mismatch_columns ARRAY<STRING> COMMENT 'Columns that differ (for mismatch rows).',
    inserted_ts      TIMESTAMP        NOT NULL COMMENT 'Row insert timestamp.',
    CONSTRAINT details_main_fk FOREIGN KEY (recon_table_id) REFERENCES main (recon_table_id)
)
CLUSTER BY (recon_table_id, recon_type)
COMMENT 'Sampled example records (NOT exhaustive; ~50-400 rows per table). Use metrics for true counts. Join to main on recon_table_id.';
