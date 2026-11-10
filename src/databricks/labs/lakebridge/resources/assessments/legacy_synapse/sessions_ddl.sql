CREATE TABLE sessions (
    session_id VARCHAR,
    status VARCHAR,
    request_id VARCHAR,
    security_id BLOB,
    login_name VARCHAR,
    login_time TIMESTAMP,
    query_count BIGINT,
    is_transactional BOOLEAN,
    client_id VARCHAR,
    app_name VARCHAR,
    sql_spid BIGINT,
    extract_ts TIMESTAMP
);
