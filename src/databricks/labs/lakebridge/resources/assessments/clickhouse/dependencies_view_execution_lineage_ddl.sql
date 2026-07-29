CREATE TABLE IF NOT EXISTS dependencies_view_execution_lineage (
    view_name VARCHAR,
    view_type VARCHAR,
    view_target VARCHAR,
    view_query VARCHAR,
    executions BIGINT,
    total_read_rows BIGINT,
    total_read_bytes BIGINT,
    total_written_rows BIGINT,
    total_written_bytes BIGINT,
    avg_duration_ms DOUBLE,
    max_memory BIGINT
);
