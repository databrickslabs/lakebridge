CREATE TABLE IF NOT EXISTS security_privileges_used (
    priv VARCHAR,
    query_count UBIGINT,
    distinct_users UBIGINT
);
