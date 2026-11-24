CREATE TABLE ranger_service_defs (
    id BIGINT,
    name VARCHAR,
    label VARCHAR,
    display_name VARCHAR,
    impl_class VARCHAR,
    is_enabled BOOLEAN,
    version BIGINT,
    access_type_count BIGINT,
    access_types VARCHAR,
    resource_names VARCHAR,
    options VARCHAR,
    create_time TIMESTAMP,
    update_time TIMESTAMP
)
