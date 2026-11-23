-- Selected system-level asynchronous metrics (system.asynchronous_metrics).
SELECT metric, value, description
FROM system.asynchronous_metrics
WHERE metric IN (
    'MaxPartCountForPartition',
    'NumberOfDatabases',
    'NumberOfTables',
    'TotalRowsOfMergeTreeTables',
    'TotalBytesOfMergeTreeTables',
    'ReplicasMaxAbsoluteDelay',
    'Uptime',
    'jemalloc.resident',
    'jemalloc.allocated',
    'OSMemoryTotal',
    'OSMemoryAvailable',
    'FilesystemMainPathAvailableBytes',
    'FilesystemMainPathTotalBytes',
    'FilesystemMainPathUsedBytes',
    'OSCPUVirtualTimeMicroseconds',
    'CPUFrequencyMHz'
)
ORDER BY metric
