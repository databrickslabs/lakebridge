-- Every queue in the scheduler tree, root included, with its depth and parent path.
-- Capacity schedulers nest children under queues.queue; fair schedulers under childQueues (childQueues.queue on
-- Hadoop 2.8+). Older capacity schedulers report the root's name as queueName only, without queuePath.
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
        tree.scheduler_type,
        COALESCE(child.q->>'$.queuePath', child.q->>'$.queueName'),
        tree.queue_path,
        tree.depth + 1,
        child.q
    FROM tree, UNNEST(child_queues(tree.node)) AS child(q)
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
    CAST(COALESCE(node->>'$.usedResources.memory', node->>'$.resourcesUsed.memory') AS BIGINT) AS used_memory_mb
FROM tree
