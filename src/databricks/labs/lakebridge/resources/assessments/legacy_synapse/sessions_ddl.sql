CREATE TABLE sessions (
    session_id VARCHAR,
    status VARCHAR,
    request_id VARCHAR,
    security_id VARCHAR,
    login_name VARCHAR,
    login_time TIMESTAMP,
    query_count BIGINT,
    is_transactional VARCHAR,
    client_id VARCHAR,
    app_name VARCHAR,
    sql_spid BIGINT,
    extract_ts TIMESTAMP
);
