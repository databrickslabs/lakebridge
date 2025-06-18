CREATE TABLE IF NOT EXISTS aggregate_details (
    recon_table_id BIGINT NOT NULL,
    rule_id BIGINT NOT NULL,
    recon_type STRING NOT NULL,
    data ARRAY<MAP<STRING, STRING>> NOT NULL,
    inserted_ts TIMESTAMP NOT NULL
);
