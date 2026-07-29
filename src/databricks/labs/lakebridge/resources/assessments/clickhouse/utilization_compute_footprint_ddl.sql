CREATE TABLE IF NOT EXISTS utilization_compute_footprint (
    total_queries BIGINT,
    total_compute_seconds DOUBLE,
    total_compute_hours DOUBLE,
    total_thread_hours DOUBLE,
    max_peak_threads BIGINT,
    avg_peak_threads DOUBLE,
    avg_query_memory_gb DOUBLE,
    max_query_memory_gb DOUBLE,
    total_data_scanned_tb DOUBLE,
    total_data_written_gb DOUBLE,
    pct_queries_over_30s DOUBLE,
    period_start DATE,
    period_end DATE
);
