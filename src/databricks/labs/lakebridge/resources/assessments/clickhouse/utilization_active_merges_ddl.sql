CREATE TABLE IF NOT EXISTS utilization_active_merges (
    database VARCHAR,
    "table" VARCHAR,
    elapsed DOUBLE,
    progress DOUBLE,
    num_parts BIGINT,
    result_part_name VARCHAR,
    total_size_bytes_compressed BIGINT,
    bytes_read_uncompressed BIGINT,
    rows_read BIGINT,
    bytes_written_uncompressed BIGINT,
    rows_written BIGINT,
    memory_usage BIGINT,
    is_mutation BIGINT
);
