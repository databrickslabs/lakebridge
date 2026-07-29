CREATE TABLE IF NOT EXISTS features_partitioned_tables (
    database VARCHAR,
    name VARCHAR,
    engine VARCHAR,
    partition_key VARCHAR,
    sorting_key VARCHAR,
    total_rows BIGINT,
    total_bytes BIGINT
);
