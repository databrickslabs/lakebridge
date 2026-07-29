CREATE TABLE IF NOT EXISTS costs_resource_footprint (
    deployment VARCHAR,
    total_compressed_gb DOUBLE,
    total_disk_gb DOUBLE,
    analysis_period_days BIGINT,
    total_compute_weight BIGINT,
    total_query_minutes DOUBLE,
    active_query_hours_of_day BIGINT,
    note VARCHAR
);
