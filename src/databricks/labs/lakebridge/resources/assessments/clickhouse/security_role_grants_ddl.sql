CREATE TABLE IF NOT EXISTS security_role_grants (
    user_name VARCHAR,
    role_name VARCHAR,
    granted_role_name VARCHAR,
    granted_role_is_default BIGINT,
    with_admin_option BIGINT
);
