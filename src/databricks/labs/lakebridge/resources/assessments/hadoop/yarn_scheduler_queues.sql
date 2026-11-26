-- Every queue in the scheduler tree, root included, with its depth and parent path. Children are under queues.queue
-- (capacity) or childQueues, possibly wrapped as childQueues.queue (fair); the root may have no queuePath.
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
        -- queuePath when reported; else a fair-scheduler queueName is the full path, and a capacity-scheduler one is
        -- joined to its parent.
        CASE
            WHEN (child.q->>'$.queuePath') IS NOT NULL THEN child.q->>'$.queuePath'
            WHEN child.parent_path IS NULL OR starts_with(child.q->>'$.queueName', child.parent_path || '.')
                THEN child.q->>'$.queueName'
            ELSE child.parent_path || '.' || (child.q->>'$.queueName')
        END,
        child.parent_path,
        child.depth + 1,
        child.q
    -- Unnested in the select list, so the parent's subtree is not copied into each child row.
    FROM (
        SELECT scheduler_type, queue_path AS parent_path, depth, unnest(child_queues(node)) AS q FROM tree
    ) AS child
)
SELECT
    scheduler_type,
    queue_path,
    parent_queue_path,
    depth,
    NULLIF(node->>'$.type', scheduler_type) AS queue_type,
    node->>'$.state' AS state,
    node->>'$.schedulingPolicy' AS scheduling_policy,
    CAST(node->>'$.capacity' AS DOUBLE) AS capacity,
    CAST(node->>'$.maxCapacity' AS DOUBLE) AS max_capacity,
    CAST(node->>'$.usedCapacity' AS DOUBLE) AS used_capacity,
    CAST(node->>'$.absoluteCapacity' AS DOUBLE) AS absolute_capacity,
    CAST(node->>'$.absoluteMaxCapacity' AS DOUBLE) AS absolute_max_capacity,
    CAST(node->>'$.weight' AS DOUBLE) AS weight,
    CAST(node->>'$.maxParallelApps' AS BIGINT) AS max_parallel_apps,
    CAST(COALESCE(node->>'$.numApplications', node->>'$.numActiveApps') AS BIGINT) AS num_applications,
    CAST(node->>'$.numPendingApps' AS BIGINT) AS num_pending_apps,
    CAST(node->>'$.allocatedContainers' AS BIGINT) AS allocated_containers,
    CAST(node->>'$.pendingContainers' AS BIGINT) AS pending_containers,
    CAST(node->>'$.preemptable' AS BOOLEAN) AS preemptable,
    CAST(node->>'$.maxResources.memory' AS BIGINT) AS max_memory_mb,
    CAST(COALESCE(node->>'$.usedResources.memory', node->>'$.resourcesUsed.memory') AS BIGINT) AS used_memory_mb,
    CAST(node->>'$.absoluteUsedCapacity' AS DOUBLE) AS absolute_used_capacity,
    CAST(COALESCE(node->>'$.numActiveApplications', node->>'$.numActiveApps') AS BIGINT) AS num_active_applications,
    CAST(node->>'$.numContainers' AS BIGINT) AS num_containers,
    CAST(node->>'$.reservedContainers' AS BIGINT) AS reserved_containers,
    CAST(COALESCE(node->>'$.maxApplications', node->>'$.maxApps') AS BIGINT) AS max_applications,
    CAST(node->>'$.maxApplicationsPerUser' AS BIGINT) AS max_applications_per_user,
    CAST(node->>'$.userLimit' AS DOUBLE) AS user_limit,
    CAST(node->>'$.userLimitFactor' AS DOUBLE) AS user_limit_factor,
    CAST(node->>'$.defaultPriority' AS BIGINT) AS default_priority,
    CAST(node->>'$.queuePriority' AS BIGINT) AS queue_priority,
    CAST(node->>'$.isAbsoluteResource' AS BOOLEAN) AS is_absolute_resource,
    node->>'$.orderingPolicyInfo' AS ordering_policy,
    CAST(node->'$.nodeLabels' AS VARCHAR) AS node_labels,
    CAST(COALESCE(node->>'$.resourcesUsed.vCores', node->>'$.usedResources.vCores') AS BIGINT) AS used_vcores,
    CAST(node->>'$.minEffectiveCapacity.memory' AS BIGINT) AS min_effective_memory_mb,
    CAST(node->>'$.minEffectiveCapacity.vCores' AS BIGINT) AS min_effective_vcores,
    CAST(node->>'$.maxEffectiveCapacity.memory' AS BIGINT) AS max_effective_memory_mb,
    CAST(node->>'$.maxEffectiveCapacity.vCores' AS BIGINT) AS max_effective_vcores,
    CAST(node->>'$.AMResourceLimit.memory' AS BIGINT) AS am_resource_limit_mb,
    CAST(node->>'$.AMResourceLimit.vCores' AS BIGINT) AS am_resource_limit_vcores,
    CAST(node->>'$.usedAMResource.memory' AS BIGINT) AS used_am_memory_mb,
    CAST(node->>'$.usedAMResource.vCores' AS BIGINT) AS used_am_vcores,
    CAST(node->>'$.userAMResourceLimit.memory' AS BIGINT) AS user_am_resource_limit_mb,
    CAST(node->>'$.userAMResourceLimit.vCores' AS BIGINT) AS user_am_resource_limit_vcores,
    CAST(COALESCE(node->>'$.maximumAllocation.memory', node->>'$.maxContainerAllocation.memory') AS BIGINT) AS max_allocation_memory_mb,
    CAST(COALESCE(node->>'$.maximumAllocation.vCores', node->>'$.maxContainerAllocation.vCores') AS BIGINT) AS max_allocation_vcores,
    CAST(node->>'$.fairResources.memory' AS BIGINT) AS fair_share_memory_mb,
    CAST(node->>'$.fairResources.vCores' AS BIGINT) AS fair_share_vcores,
    CAST(node->>'$.steadyFairResources.memory' AS BIGINT) AS steady_fair_share_memory_mb,
    CAST(node->>'$.steadyFairResources.vCores' AS BIGINT) AS steady_fair_share_vcores,
    CAST(node->>'$.minResources.memory' AS BIGINT) AS min_resources_memory_mb,
    CAST(node->>'$.minResources.vCores' AS BIGINT) AS min_resources_vcores,
    CAST(node->>'$.maxResources.vCores' AS BIGINT) AS max_resources_vcores,
    CAST(node->>'$.demandResources.memory' AS BIGINT) AS demand_memory_mb,
    CAST(node->>'$.demandResources.vCores' AS BIGINT) AS demand_vcores,
    CAST(node->'$.users' AS VARCHAR) AS queue_users
FROM tree
