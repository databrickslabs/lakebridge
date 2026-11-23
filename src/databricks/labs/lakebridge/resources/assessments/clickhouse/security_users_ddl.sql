CREATE TABLE IF NOT EXISTS security_users (
    name VARCHAR,
    storage VARCHAR,
    auth_type VARCHAR,
    auth_params VARCHAR,
    host_ip VARCHAR,
    host_names VARCHAR,
    host_names_regexp VARCHAR,
    host_names_like VARCHAR,
    default_roles_all BIGINT,
    default_roles_list VARCHAR,
    default_roles_except VARCHAR,
    grantees_any BIGINT,
    grantees_list VARCHAR,
    grantees_except VARCHAR,
    default_database VARCHAR
);
