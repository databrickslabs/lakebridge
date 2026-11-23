CREATE TABLE IF NOT EXISTS objects_parts_summary (
    database VARCHAR,
    "table" VARCHAR,
    active_parts UBIGINT,
    total_rows UBIGINT,
    total_bytes_on_disk UBIGINT,
    size_on_disk VARCHAR,
    data_compressed UBIGINT,
    data_uncompressed UBIGINT,
    compression_ratio DOUBLE,
    min_date DATE,
    max_date DATE,
    partition_count UBIGINT
);
