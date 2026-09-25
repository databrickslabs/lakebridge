CREATE TABLE IF NOT EXISTS security_session_activity (
    type VARCHAR,
    user VARCHAR,
    auth_type VARCHAR,
    event_count UBIGINT,
    distinct_hosts UBIGINT,
    first_seen TIMESTAMP,
    last_seen TIMESTAMP
);
