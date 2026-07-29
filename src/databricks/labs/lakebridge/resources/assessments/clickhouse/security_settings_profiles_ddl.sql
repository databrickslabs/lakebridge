CREATE TABLE IF NOT EXISTS security_settings_profiles (
    name VARCHAR,
    storage VARCHAR,
    num_elements BIGINT,
    apply_to_all BIGINT,
    apply_to_list VARCHAR,
    apply_to_except VARCHAR
);
