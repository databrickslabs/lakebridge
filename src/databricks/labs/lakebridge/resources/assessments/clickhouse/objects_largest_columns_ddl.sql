CREATE TABLE IF NOT EXISTS objects_largest_columns (
    database VARCHAR,
    "table" VARCHAR,
    column_name VARCHAR,
    "type" VARCHAR,
    compressed_bytes UBIGINT,
    uncompressed_bytes UBIGINT,
    compressed_readable VARCHAR,
    compression_ratio DOUBLE
);
