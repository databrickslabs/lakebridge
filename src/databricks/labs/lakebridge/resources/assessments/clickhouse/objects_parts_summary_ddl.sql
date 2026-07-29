CREATE TABLE IF NOT EXISTS objects_parts_summary (
    database VARCHAR,
    "table" VARCHAR,
    active_parts BIGINT,
    total_rows BIGINT,
    total_bytes_on_disk BIGINT,
    size_on_disk VARCHAR,
    data_compressed BIGINT,
    data_uncompressed BIGINT,
    compression_ratio DOUBLE,
    min_date DATE,
    max_date DATE,
    partition_count BIGINT
);
