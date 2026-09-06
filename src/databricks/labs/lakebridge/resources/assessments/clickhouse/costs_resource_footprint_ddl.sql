CREATE TABLE IF NOT EXISTS costs_resource_footprint (
    deployment VARCHAR,
    total_compressed_gb DOUBLE,
    total_disk_gb DOUBLE,
    analysis_period_days UBIGINT,
    total_compute_weight UBIGINT,
    total_query_minutes DOUBLE,
    active_query_hours_of_day UBIGINT,
    note VARCHAR
);
