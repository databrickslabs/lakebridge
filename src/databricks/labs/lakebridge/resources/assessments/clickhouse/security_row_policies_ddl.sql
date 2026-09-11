CREATE TABLE IF NOT EXISTS security_row_policies (
    name VARCHAR,
    short_name VARCHAR,
    database VARCHAR,
    "table" VARCHAR,
    id UUID,
    storage VARCHAR,
    select_filter VARCHAR,
    is_restrictive BIGINT,
    apply_to_all BIGINT,
    apply_to_list VARCHAR,
    apply_to_except VARCHAR
);
