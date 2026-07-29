CREATE TABLE IF NOT EXISTS costs_storage_by_table (
    database VARCHAR,
    "table" VARCHAR,
    bytes_on_disk BIGINT,
    compressed_bytes BIGINT,
    uncompressed_bytes BIGINT,
    rows BIGINT,
    compression_ratio DOUBLE,
    active_parts BIGINT,
    compressed_gb DOUBLE,
    disk_gb DOUBLE
);
