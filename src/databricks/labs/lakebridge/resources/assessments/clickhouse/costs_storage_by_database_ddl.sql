CREATE TABLE IF NOT EXISTS costs_storage_by_database (
    database VARCHAR,
    bytes_on_disk BIGINT,
    compressed_bytes BIGINT,
    uncompressed_bytes BIGINT,
    total_rows BIGINT,
    table_count BIGINT,
    compressed_gb DOUBLE
);
