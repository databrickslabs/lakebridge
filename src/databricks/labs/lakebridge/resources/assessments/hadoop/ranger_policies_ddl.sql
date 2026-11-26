CREATE TABLE ranger_policies (
    id BIGINT,
    guid VARCHAR,
    service VARCHAR,
    service_type VARCHAR,
    name VARCHAR,
    description VARCHAR,
    policy_type_code BIGINT,
    policy_type VARCHAR,
    priority BIGINT,
    is_enabled BOOLEAN,
    is_audit_enabled BOOLEAN,
    is_deny_all_else BOOLEAN,
    version BIGINT,
    resources VARCHAR,
    conditions VARCHAR,
    validity_schedules VARCHAR,
    policy_labels VARCHAR
)
