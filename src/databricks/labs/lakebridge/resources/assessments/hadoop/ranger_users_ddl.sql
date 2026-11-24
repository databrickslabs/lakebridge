CREATE TABLE ranger_users (
    id BIGINT,
    name VARCHAR,
    status BIGINT,
    is_visible BIGINT,
    user_source BIGINT,
    group_names VARCHAR,
    role_names VARCHAR,
    create_time TIMESTAMP,
    update_time TIMESTAMP
)
