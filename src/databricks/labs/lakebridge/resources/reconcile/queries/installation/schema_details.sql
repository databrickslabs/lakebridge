CREATE TABLE IF NOT EXISTS schema_details (
    recon_table_id      BIGINT    NOT NULL COMMENT 'Join key to main.',
    source_column       STRING COMMENT 'Source column name.',
    source_datatype     STRING COMMENT 'Source column data type.',
    databricks_column   STRING COMMENT 'Mapped Databricks column name.',
    databricks_datatype STRING COMMENT 'Databricks column data type.',
    is_valid            BOOLEAN COMMENT 'False means type/name mismatch for this column.',
    inserted_ts         TIMESTAMP NOT NULL COMMENT 'Row insert timestamp.'
)
COMMENT 'Per-column schema comparison (one row per compared column). Join to main on recon_table_id.';
