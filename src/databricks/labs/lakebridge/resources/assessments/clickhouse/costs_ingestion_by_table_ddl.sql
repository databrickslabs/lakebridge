CREATE TABLE IF NOT EXISTS costs_ingestion_by_table (
    tbl VARCHAR,
    insert_count UBIGINT,
    total_written_bytes UBIGINT,
    total_written_rows UBIGINT,
    total_duration_ms UBIGINT
);
