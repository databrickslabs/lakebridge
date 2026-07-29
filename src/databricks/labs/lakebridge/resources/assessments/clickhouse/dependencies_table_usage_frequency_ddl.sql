CREATE TABLE IF NOT EXISTS dependencies_table_usage_frequency (
    tbl VARCHAR,
    access_count BIGINT,
    distinct_users BIGINT,
    query_kinds BIGINT,
    total_read_bytes BIGINT,
    query_kind_list VARCHAR
);
