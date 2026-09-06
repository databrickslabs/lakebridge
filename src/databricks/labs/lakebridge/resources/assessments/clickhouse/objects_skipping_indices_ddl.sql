CREATE TABLE IF NOT EXISTS objects_skipping_indices (
    database VARCHAR,
    "table" VARCHAR,
    index_name VARCHAR,
    index_type VARCHAR,
    expr VARCHAR,
    granularity UBIGINT,
    data_compressed_bytes UBIGINT,
    data_uncompressed_bytes UBIGINT
);
