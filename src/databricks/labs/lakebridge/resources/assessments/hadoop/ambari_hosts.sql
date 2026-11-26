-- Ambari hosts: cpu, memory (total_mem unconverted) and OS as reported; disk_info kept, its sizes summed into totals.
WITH hosts AS (
    SELECT h, h->>'$.Hosts.host_name' AS host_name
    FROM (SELECT unnest(hadoop_records(doc, '$.items')) AS h FROM raw_documents) AS t
),
disks AS (
    SELECT hs.host_name, disk
    FROM hosts AS hs, UNNEST(hadoop_records(hs.h, '$.Hosts.disk_info')) AS d(disk)
),
disk_totals AS (
    SELECT
        host_name,
        SUM(TRY_CAST(disk->>'$.size' AS BIGINT)) AS disk_size_total,
        SUM(TRY_CAST(disk->>'$.available' AS BIGINT)) AS disk_available_total
    FROM disks
    GROUP BY host_name
)
SELECT
    hs.h->>'$.Hosts.cluster_name' AS cluster_name,
    hs.host_name,
    CAST(hs.h->>'$.Hosts.cpu_count' AS BIGINT) AS cpu_count,
    CAST(hs.h->>'$.Hosts.total_mem' AS BIGINT) AS total_mem,
    hs.h->>'$.Hosts.os_type' AS os_type,
    dt.disk_size_total,
    dt.disk_available_total,
    CAST(hs.h->'$.Hosts.disk_info' AS VARCHAR) AS disk_info
FROM hosts AS hs
LEFT JOIN disk_totals AS dt ON dt.host_name = hs.host_name
