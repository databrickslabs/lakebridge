CREATE TABLE ranger_services (
    id BIGINT,
    name VARCHAR,
    service_type VARCHAR,
    display_name VARCHAR,
    description VARCHAR,
    is_enabled BOOLEAN,
    tag_service VARCHAR,
    version BIGINT,
    policy_version BIGINT,
    tag_version BIGINT,
    create_time TIMESTAMP,
    update_time TIMESTAMP,
    created_by VARCHAR,
    updated_by VARCHAR,
    configs VARCHAR
)
