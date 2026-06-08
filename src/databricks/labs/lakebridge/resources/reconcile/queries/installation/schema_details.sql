CREATE TABLE IF NOT EXISTS schema_details (
    recon_table_id      BIGINT    NOT NULL,
    source_column       STRING,
    source_datatype     STRING,
    databricks_column   STRING,
    databricks_datatype STRING,
    is_valid            BOOLEAN,
    inserted_ts         TIMESTAMP NOT NULL
);
