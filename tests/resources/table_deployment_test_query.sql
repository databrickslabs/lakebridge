CREATE TABLE IF NOT EXISTS details (
    recon_table_id   BIGINT          NOT NULL,
    recon_type       STRING          NOT NULL,
    record_key       VARIANT,
    source_row       VARIANT,
    target_row       VARIANT,
    mismatch_columns ARRAY<STRING>,
    inserted_ts      TIMESTAMP        NOT NULL
);
