-- One row per scheduler queue and node label partition (capacity scheduler): the configured, effective and used
-- capacity of each partition. The default partition has an empty name. Fair schedulers have no partitions.
WITH RECURSIVE tree(scheduler_type, queue_path, parent_queue_path, depth, node) AS (
    SELECT
        doc->>'$.scheduler.schedulerInfo.type',
        COALESCE(
            json_extract_string(doc, '$.scheduler.schedulerInfo.rootQueue.queueName'),
            json_extract_string(doc, '$.scheduler.schedulerInfo.queuePath'),
            json_extract_string(doc, '$.scheduler.schedulerInfo.queueName')
        ),
        CAST(NULL AS VARCHAR),
        0,
        COALESCE(json_extract(doc, '$.scheduler.schedulerInfo.rootQueue'), json_extract(doc, '$.scheduler.schedulerInfo'))
    FROM raw_documents
    UNION ALL
    SELECT
        child.scheduler_type,
        -- queuePath when the scheduler reports it. Otherwise a fair-scheduler queueName is already the full path
        -- (root.default), and a capacity-scheduler queueName (Hadoop 3.3 and older: default) is joined to its parent.
        CASE
            WHEN (child.q->>'$.queuePath') IS NOT NULL THEN child.q->>'$.queuePath'
            WHEN child.parent_path IS NULL OR starts_with(child.q->>'$.queueName', child.parent_path || '.')
                THEN child.q->>'$.queueName'
            ELSE child.parent_path || '.' || (child.q->>'$.queueName')
        END,
        child.parent_path,
        child.depth + 1,
        child.q
    -- The children are unnested in the select list, so the parent's subtree is not copied into every child row.
    FROM (
        SELECT scheduler_type, queue_path AS parent_path, depth, unnest(child_queues(node)) AS q FROM tree
    ) AS child
),
caps AS (
    SELECT
        scheduler_type,
        queue_path,
        hadoop_records(node, '$.resources.resourceUsagesByPartition') AS usages,
        unnest(hadoop_records(node, '$.capacities.queueCapacitiesByPartition')) AS cap
    FROM tree
),
partitions AS (
    SELECT scheduler_type, queue_path, cap,
        list_filter(usages, u -> (u->>'$.partitionName') IS NOT DISTINCT FROM (cap->>'$.partitionName'))[1] AS use
    FROM caps
)
SELECT
    scheduler_type,
    queue_path,
    cap->>'$.partitionName' AS partition_name,
    CAST(cap->>'$.capacity' AS DOUBLE) AS capacity,
    CAST(cap->>'$.usedCapacity' AS DOUBLE) AS used_capacity,
    CAST(cap->>'$.maxCapacity' AS DOUBLE) AS max_capacity,
    CAST(cap->>'$.absoluteCapacity' AS DOUBLE) AS absolute_capacity,
    CAST(cap->>'$.absoluteUsedCapacity' AS DOUBLE) AS absolute_used_capacity,
    CAST(cap->>'$.absoluteMaxCapacity' AS DOUBLE) AS absolute_max_capacity,
    CAST(cap->>'$.maxAMLimitPercentage' AS DOUBLE) AS max_am_limit_percentage,
    CAST(cap->>'$.weight' AS DOUBLE) AS weight,
    CAST(cap->>'$.normalizedWeight' AS DOUBLE) AS normalized_weight,
    cap->>'$.queueCapacityVectorInfo.configuredCapacityVector' AS configured_capacity_vector,
    CAST(cap->>'$.configuredMinResource.memory' AS BIGINT) AS configured_min_memory_mb,
    CAST(cap->>'$.configuredMinResource.vCores' AS BIGINT) AS configured_min_vcores,
    CAST(cap->>'$.configuredMaxResource.memory' AS BIGINT) AS configured_max_memory_mb,
    CAST(cap->>'$.configuredMaxResource.vCores' AS BIGINT) AS configured_max_vcores,
    CAST(cap->>'$.effectiveMinResource.memory' AS BIGINT) AS effective_min_memory_mb,
    CAST(cap->>'$.effectiveMinResource.vCores' AS BIGINT) AS effective_min_vcores,
    CAST(cap->>'$.effectiveMaxResource.memory' AS BIGINT) AS effective_max_memory_mb,
    CAST(cap->>'$.effectiveMaxResource.vCores' AS BIGINT) AS effective_max_vcores,
    CAST(use->>'$.used.memory' AS BIGINT) AS used_memory_mb,
    CAST(use->>'$.used.vCores' AS BIGINT) AS used_vcores,
    CAST(use->>'$.reserved.memory' AS BIGINT) AS reserved_memory_mb,
    CAST(use->>'$.reserved.vCores' AS BIGINT) AS reserved_vcores,
    CAST(use->>'$.pending.memory' AS BIGINT) AS pending_memory_mb,
    CAST(use->>'$.pending.vCores' AS BIGINT) AS pending_vcores,
    CAST(use->>'$.amUsed.memory' AS BIGINT) AS am_used_memory_mb,
    CAST(use->>'$.amUsed.vCores' AS BIGINT) AS am_used_vcores,
    CAST(use->>'$.amLimit.memory' AS BIGINT) AS am_limit_memory_mb,
    CAST(use->>'$.amLimit.vCores' AS BIGINT) AS am_limit_vcores,
    CAST(use->>'$.userAmLimit.memory' AS BIGINT) AS user_am_limit_memory_mb,
    CAST(use->>'$.userAmLimit.vCores' AS BIGINT) AS user_am_limit_vcores
FROM partitions
