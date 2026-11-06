CREATE TABLE requests (
    request_id VARCHAR,
    session_id VARCHAR,
    status VARCHAR,
    submit_time TIMESTAMP,
    start_time TIMESTAMP,
    end_compile_time TIMESTAMP,
    end_time TIMESTAMP,
    total_elapsed_time BIGINT,
    label VARCHAR,
    error_id VARCHAR,
    database_id BIGINT,
    command VARCHAR,
    resource_class VARCHAR,
    extract_ts TIMESTAMP
);
