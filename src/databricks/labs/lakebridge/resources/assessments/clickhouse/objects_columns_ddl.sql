CREATE TABLE IF NOT EXISTS objects_columns (
    database VARCHAR,
    "table" VARCHAR,
    column_name VARCHAR,
    "type" VARCHAR,
    default_kind VARCHAR,
    default_expression VARCHAR,
    data_compressed_bytes UBIGINT,
    data_uncompressed_bytes UBIGINT,
    marks_bytes UBIGINT,
    comment VARCHAR,
    is_in_partition_key BIGINT,
    is_in_sorting_key BIGINT,
    is_in_primary_key BIGINT,
    is_in_sampling_key BIGINT
);
