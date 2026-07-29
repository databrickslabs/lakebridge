CREATE TABLE IF NOT EXISTS security_session_activity (
    type VARCHAR,
    user VARCHAR,
    auth_type VARCHAR,
    event_count BIGINT,
    distinct_hosts BIGINT,
    first_seen TIMESTAMP,
    last_seen TIMESTAMP
);
