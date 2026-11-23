CREATE TABLE IF NOT EXISTS dependencies_view_execution_lineage (
    view_name VARCHAR,
    view_type VARCHAR,
    view_target VARCHAR,
    view_query VARCHAR,
    executions UBIGINT,
    total_read_rows UBIGINT,
    total_read_bytes UBIGINT,
    total_written_rows UBIGINT,
    total_written_bytes UBIGINT,
    avg_duration_ms DOUBLE,
    max_memory UBIGINT
);
