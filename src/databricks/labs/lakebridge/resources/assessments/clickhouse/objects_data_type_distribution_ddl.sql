CREATE TABLE IF NOT EXISTS objects_data_type_distribution (
    "type" VARCHAR,
    usage_count UBIGINT,
    distinct_tables UBIGINT,
    total_compressed_bytes UBIGINT,
    total_uncompressed_bytes UBIGINT
);
