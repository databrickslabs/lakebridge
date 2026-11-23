CREATE TABLE IF NOT EXISTS security_quotas (
    name VARCHAR,
    id UUID,
    storage VARCHAR,
    keys VARCHAR,
    durations VARCHAR,
    apply_to_all BIGINT,
    apply_to_list VARCHAR,
    apply_to_except VARCHAR
);
