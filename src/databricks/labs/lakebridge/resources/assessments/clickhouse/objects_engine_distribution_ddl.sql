CREATE TABLE IF NOT EXISTS objects_engine_distribution (
    database VARCHAR,
    engine VARCHAR,
    object_count UBIGINT,
    sum_rows UBIGINT,
    sum_bytes UBIGINT,
    total_size_readable VARCHAR
);
