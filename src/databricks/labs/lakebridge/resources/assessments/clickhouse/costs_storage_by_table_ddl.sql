CREATE TABLE IF NOT EXISTS costs_storage_by_table (
    database VARCHAR,
    "table" VARCHAR,
    bytes_on_disk UBIGINT,
    compressed_bytes UBIGINT,
    uncompressed_bytes UBIGINT,
    rows UBIGINT,
    compression_ratio DOUBLE,
    active_parts UBIGINT,
    compressed_gb DOUBLE,
    disk_gb DOUBLE
);
