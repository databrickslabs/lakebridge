-- Ambari NameNode, DataNode, ResourceManager and NodeManager: one row per metric leaf, or one NULL-metric row when the
-- component has none (metrics need Ambari Metrics). metric_path is relative to "metrics".
SELECT
    info->>'$.cluster_name' AS cluster_name,
    info->>'$.service_name' AS service_name,
    info->>'$.component_name' AS component_name,
    info->>'$.category' AS category,
    info->>'$.display_name' AS display_name,
    info->>'$.state' AS state,
    info->>'$.desired_stack' AS desired_stack,
    info->>'$.desired_version' AS desired_version,
    info->>'$.repository_state' AS repository_state,
    CAST(info->>'$.total_count' AS BIGINT) AS total_count,
    CAST(info->>'$.installed_count' AS BIGINT) AS installed_count,
    CAST(info->>'$.started_count' AS BIGINT) AS started_count,
    CAST(info->>'$.init_count' AS BIGINT) AS init_count,
    CAST(info->>'$.install_failed_count' AS BIGINT) AS install_failed_count,
    CAST(info->>'$.installed_and_maintenance_off_count' AS BIGINT) AS installed_and_maintenance_off_count,
    CAST(info->>'$.unknown_count' AS BIGINT) AS unknown_count,
    leaf->>'$.metric_path' AS metric_path,
    leaf->>'$.metric_value' AS metric_value
FROM (
    SELECT
        doc->'$.component.ServiceComponentInfo' AS info,
        unnest(
            CASE WHEN json_array_length(doc, '$.metric_leaves') > 0
                THEN hadoop_records(doc, '$.metric_leaves')
                ELSE CAST([NULL] AS JSON[])
            END) AS leaf
    FROM raw_documents
) AS m
