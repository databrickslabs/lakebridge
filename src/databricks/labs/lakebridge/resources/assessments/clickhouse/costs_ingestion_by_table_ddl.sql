CREATE TABLE IF NOT EXISTS costs_ingestion_by_table (
    tbl VARCHAR,
    insert_count BIGINT,
    total_written_bytes BIGINT,
    total_written_rows BIGINT,
    total_duration_ms BIGINT
);
