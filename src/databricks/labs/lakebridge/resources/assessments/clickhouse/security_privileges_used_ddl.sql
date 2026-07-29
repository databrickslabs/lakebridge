CREATE TABLE IF NOT EXISTS security_privileges_used (
    priv VARCHAR,
    query_count BIGINT,
    distinct_users BIGINT
);
