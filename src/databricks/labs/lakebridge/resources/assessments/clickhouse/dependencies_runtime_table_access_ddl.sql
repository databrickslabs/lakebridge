CREATE TABLE IF NOT EXISTS dependencies_runtime_table_access (
    user VARCHAR,
    normalized_query_hash UBIGINT,
    query_kind VARCHAR,
    accessed_databases VARCHAR,
    accessed_tables VARCHAR,
    runs BIGINT,
    total_read_bytes BIGINT
);
