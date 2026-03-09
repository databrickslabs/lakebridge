-- Multiple DDL statements in a single file
CREATE TABLE inventory (
    db_id INTEGER,
    name VARCHAR,
    collation_name VARCHAR,
    create_date TIMESTAMP,
    extract_ts TIMESTAMP
);

CREATE TABLE usage (
    sql_handle VARCHAR,
    creation_time TIMESTAMP,
    last_execution_time TIMESTAMP,
    execution_count BIGINT,
    total_worker_time BIGINT,
    total_elapsed_time BIGINT,
    total_rows BIGINT
);

CREATE TABLE metadata (
    pipeline_name VARCHAR,
    execution_date TIMESTAMP,
    version VARCHAR
);
