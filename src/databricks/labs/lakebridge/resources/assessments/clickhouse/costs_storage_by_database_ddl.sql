CREATE TABLE IF NOT EXISTS costs_storage_by_database (
    database VARCHAR,
    bytes_on_disk UBIGINT,
    compressed_bytes UBIGINT,
    uncompressed_bytes UBIGINT,
    total_rows UBIGINT,
    table_count UBIGINT,
    compressed_gb DOUBLE
);
