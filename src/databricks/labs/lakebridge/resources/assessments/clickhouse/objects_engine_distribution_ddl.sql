CREATE TABLE IF NOT EXISTS objects_engine_distribution (
    database VARCHAR,
    engine VARCHAR,
    object_count BIGINT,
    sum_rows BIGINT,
    sum_bytes BIGINT,
    total_size_readable VARCHAR
);
