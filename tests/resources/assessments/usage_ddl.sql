CREATE TABLE usage (
    sql_handle VARCHAR,
    creation_time TIMESTAMP,
    last_execution_time TIMESTAMP,
    execution_count BIGINT,
    total_worker_time BIGINT,
    total_elapsed_time BIGINT,
    total_rows BIGINT
);
