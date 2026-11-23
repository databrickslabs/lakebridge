CREATE TABLE IF NOT EXISTS dependencies_table_usage_frequency (
    tbl VARCHAR,
    access_count UBIGINT,
    distinct_users UBIGINT,
    query_kinds UBIGINT,
    total_read_bytes UBIGINT,
    query_kind_list VARCHAR
);
