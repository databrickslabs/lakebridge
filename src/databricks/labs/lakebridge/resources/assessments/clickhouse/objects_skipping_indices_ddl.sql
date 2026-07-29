CREATE TABLE IF NOT EXISTS objects_skipping_indices (
    database VARCHAR,
    "table" VARCHAR,
    index_name VARCHAR,
    index_type VARCHAR,
    expr VARCHAR,
    granularity BIGINT,
    data_compressed_bytes BIGINT,
    data_uncompressed_bytes BIGINT
);
