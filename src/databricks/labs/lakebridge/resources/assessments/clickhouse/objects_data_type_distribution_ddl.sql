CREATE TABLE IF NOT EXISTS objects_data_type_distribution (
    "type" VARCHAR,
    usage_count BIGINT,
    distinct_tables BIGINT,
    total_compressed_bytes BIGINT,
    total_uncompressed_bytes BIGINT
);
