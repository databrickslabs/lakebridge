CREATE TABLE IF NOT EXISTS aggregate_details (
    recon_table_id   BIGINT          NOT NULL,
    rule_id          BIGINT          NOT NULL,
    recon_type       STRING          NOT NULL,
    record_key       VARIANT,
    source_row       VARIANT,
    target_row       VARIANT,
    mismatch_columns ARRAY<STRING>,
    inserted_ts      TIMESTAMP        NOT NULL
)
CLUSTER BY (recon_table_id, rule_id);
