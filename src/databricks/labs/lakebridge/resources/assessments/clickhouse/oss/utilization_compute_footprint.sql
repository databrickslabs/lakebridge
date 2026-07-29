-- Aggregate compute footprint over the window (query_log).
SELECT
    count() AS total_queries,
    round(sum(query_duration_ms) / 1000.0, 2) AS total_compute_seconds,
    round(sum(query_duration_ms) / 3600000.0, 2) AS total_compute_hours,
    round(sum(query_duration_ms * greatest(peak_threads_usage, 1)) / 3600000.0, 2) AS total_thread_hours,
    max(peak_threads_usage) AS max_peak_threads,
    round(avg(peak_threads_usage), 2) AS avg_peak_threads,
    round(avg(memory_usage) / (1024*1024*1024), 2) AS avg_query_memory_gb,
    round(max(memory_usage) / (1024*1024*1024), 2) AS max_query_memory_gb,
    round(sum(read_bytes) / (1024*1024*1024*1024), 2) AS total_data_scanned_tb,
    round(sum(written_bytes) / (1024*1024*1024), 2) AS total_data_written_gb,
    round(countIf(query_duration_ms > 30000) * 100.0 / count(), 2) AS pct_queries_over_30s,
    toStartOfMonth(min(event_time)) AS period_start,
    toStartOfMonth(max(event_time)) AS period_end
FROM system.query_log
WHERE type = 'QueryFinish'
  AND is_initial_query = 1
  AND event_time >= now() - INTERVAL 30 DAY
