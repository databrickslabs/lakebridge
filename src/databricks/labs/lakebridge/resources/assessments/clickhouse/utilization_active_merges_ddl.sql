CREATE TABLE IF NOT EXISTS utilization_active_merges (
    database VARCHAR,
    "table" VARCHAR,
    elapsed DOUBLE,
    progress DOUBLE,
    num_parts UBIGINT,
    result_part_name VARCHAR,
    total_size_bytes_compressed UBIGINT,
    bytes_read_uncompressed UBIGINT,
    rows_read UBIGINT,
    bytes_written_uncompressed UBIGINT,
    rows_written UBIGINT,
    memory_usage UBIGINT,
    is_mutation BIGINT
);
