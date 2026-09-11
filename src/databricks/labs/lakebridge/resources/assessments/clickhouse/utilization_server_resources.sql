-- Server hardware profile from asynchronous metrics (system.asynchronous_metrics).
SELECT
    (SELECT value FROM system.asynchronous_metrics WHERE metric = 'OSMemoryTotal') AS total_memory_bytes,
    (SELECT value FROM system.asynchronous_metrics WHERE metric = 'OSMemoryAvailable') AS available_memory_bytes,
    (SELECT round(value / (1024*1024*1024), 2) FROM system.asynchronous_metrics WHERE metric = 'OSMemoryTotal') AS total_memory_gb,
    (SELECT value FROM system.asynchronous_metrics WHERE metric = 'Uptime') AS uptime_seconds,
    (SELECT value FROM system.asynchronous_metrics WHERE metric = 'FilesystemMainPathTotalBytes') AS fs_total_bytes,
    (SELECT value FROM system.asynchronous_metrics WHERE metric = 'FilesystemMainPathAvailableBytes') AS fs_available_bytes
