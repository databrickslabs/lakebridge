CREATE TABLE IF NOT EXISTS features_available_table_engines (
    name VARCHAR,
    supports_settings BIGINT,
    supports_skipping_indices BIGINT,
    supports_projections BIGINT,
    supports_sort_order BIGINT,
    supports_replication BIGINT
);
