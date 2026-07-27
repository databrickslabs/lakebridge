CREATE TABLE IF NOT EXISTS aggregate_details (
    recon_table_id   BIGINT          NOT NULL COMMENT 'Join key to main.',
    rule_id          BIGINT          NOT NULL COMMENT 'Join key to aggregate_rules.',
    recon_type       STRING          NOT NULL COMMENT 'mismatch | missing_in_source | missing_in_target.',
    record_key       VARIANT COMMENT 'Group-by values identifying the sampled aggregate group.',
    source_row       VARIANT COMMENT 'Source-side aggregate image.',
    target_row       VARIANT COMMENT 'Target-side aggregate image.',
    mismatch_columns ARRAY<STRING> COMMENT 'Aggregate columns that differ.',
    inserted_ts      TIMESTAMP        NOT NULL COMMENT 'Row insert timestamp.'
)
CLUSTER BY (recon_table_id, rule_id)
COMMENT 'Sampled example aggregate groups for aggregates-reconcile. Join main on recon_table_id, aggregate_rules on rule_id.';
