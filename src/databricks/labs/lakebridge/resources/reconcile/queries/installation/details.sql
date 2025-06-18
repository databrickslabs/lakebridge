CREATE TABLE IF NOT EXISTS details (
    recon_table_id BIGINT NOT NULL,
    recon_type STRING NOT NULL,
    status BOOLEAN NOT NULL,
    data ARRAY<MAP<STRING, STRING>> NOT NULL,
    inserted_ts TIMESTAMP NOT NULL
);
