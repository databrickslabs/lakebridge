CREATE TABLE IF NOT EXISTS security_grants (
    user_name VARCHAR,
    role_name VARCHAR,
    access_type VARCHAR,
    database VARCHAR,
    "table" VARCHAR,
    "column" VARCHAR,
    is_partial_revoke BIGINT,
    grant_option BIGINT
);
