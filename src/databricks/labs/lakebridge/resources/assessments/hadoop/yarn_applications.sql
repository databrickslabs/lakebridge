-- One row per YARN application.
-- Like the original Hadoop profiler, the application name and diagnostics are kept as the ResourceManager reports
-- them (empty becomes NULL). Mask them with hadoop.search_replace_file if needed.
-- The RM reports -1 for allocation fields that do not apply; those become NULL.
-- The RM can report one application in both the finished and the unfinished response, so keep one row per id,
-- preferring the one that has finished.
WITH apps AS (
    SELECT t.a AS a
    FROM (SELECT unnest(hadoop_records(doc, '$.apps.app')) AS a FROM raw_documents) AS t
),
latest AS (
    SELECT a, ROW_NUMBER() OVER (
        PARTITION BY a->>'$.id'
        ORDER BY NULLIF(CAST(a->>'$.finishedTime' AS BIGINT), 0) DESC NULLS LAST
    ) AS rn
    FROM apps
)
SELECT
    a->>'$.id' AS application_id,
    a->>'$.user' AS user_name,
    NULLIF(a->>'$.name', '') AS name,
    a->>'$.queue' AS queue_name,
    a->>'$.applicationType' AS application_type,
    a->>'$.state' AS state,
    a->>'$.finalStatus' AS final_status,
    CAST(a->>'$.progress' AS DOUBLE) AS progress,
    NULLIF(a->>'$.diagnostics', '') AS diagnostics,
    epoch_ms(NULLIF(CAST(a->>'$.startedTime' AS BIGINT), 0)) AS started_time,
    epoch_ms(NULLIF(CAST(a->>'$.launchTime' AS BIGINT), 0)) AS launch_time,
    epoch_ms(NULLIF(CAST(a->>'$.finishedTime' AS BIGINT), 0)) AS finished_time,
    CAST(a->>'$.elapsedTime' AS BIGINT) AS elapsed_time_ms,
    NULLIF(CAST(a->>'$.allocatedMB' AS BIGINT), -1) AS allocated_mb,
    NULLIF(CAST(a->>'$.allocatedVCores' AS BIGINT), -1) AS allocated_vcores,
    NULLIF(CAST(a->>'$.runningContainers' AS BIGINT), -1) AS running_containers,
    CAST(a->>'$.memorySeconds' AS BIGINT) AS memory_seconds,
    CAST(a->>'$.vcoreSeconds' AS BIGINT) AS vcore_seconds,
    CAST(a->>'$.preemptedResourceMB' AS BIGINT) AS preempted_resource_mb,
    CAST(a->>'$.preemptedResourceVCores' AS BIGINT) AS preempted_resource_vcores,
    CAST(a->>'$.numAMContainerPreempted' AS BIGINT) AS num_am_container_preempted,
    CAST(a->>'$.numNonAMContainerPreempted' AS BIGINT) AS num_non_am_container_preempted,
    CAST(a->>'$.queueUsagePercentage' AS DOUBLE) AS queue_usage_percentage,
    CAST(a->>'$.clusterUsagePercentage' AS DOUBLE) AS cluster_usage_percentage,
    a->>'$.logAggregationStatus' AS log_aggregation_status,
    CAST(a->>'$.unmanagedApplication' AS BOOLEAN) AS unmanaged_application,
    CAST(a->>'$.priority' AS BIGINT) AS priority,
    -- Resource-seconds per resource type (memory-mb, vcores, GPUs and other custom resources).
    yarn_resource_map(a->'$.resourceSecondsMap') AS resource_seconds,
    yarn_resource_map(a->'$.preemptedResourceSecondsMap') AS preempted_resource_seconds,
    CAST(a->>'$.preemptedMemorySeconds' AS BIGINT) AS preempted_memory_seconds,
    CAST(a->>'$.preemptedVcoreSeconds' AS BIGINT) AS preempted_vcore_seconds,
    NULLIF(CAST(a->>'$.reservedMB' AS BIGINT), -1) AS reserved_mb,
    NULLIF(CAST(a->>'$.reservedVCores' AS BIGINT), -1) AS reserved_vcores,
    NULLIF(a->>'$.amHostHttpAddress', '') AS am_host_http_address,
    NULLIF(a->>'$.applicationTags', '') AS application_tags,
    NULLIF(a->>'$.amNodeLabelExpression', '') AS am_node_label_expression,
    NULLIF(a->>'$.appNodeLabelExpression', '') AS app_node_label_expression,
    a->>'$.clusterId' AS cluster_id
FROM latest
WHERE rn = 1
