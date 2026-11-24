CREATE TABLE cm_services (
    cluster_name VARCHAR,
    service_name VARCHAR,
    display_name VARCHAR,
    service_type VARCHAR,
    service_state VARCHAR,
    health_summary VARCHAR,
    entity_status VARCHAR,
    maintenance_mode BOOLEAN,
    config_staleness_status VARCHAR,
    health_checks VARCHAR
)
