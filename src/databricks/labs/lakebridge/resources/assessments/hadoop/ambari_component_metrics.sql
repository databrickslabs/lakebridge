-- Ambari NameNode, DataNode, ResourceManager and NodeManager: one row per metric leaf of each component, or one row with
-- a NULL metric when the component has no metrics. Ambari reports metrics only when the Ambari Metrics service runs.
-- The metric_path is relative to the component's "metrics" object (for example dfs.namenode.CapacityTotal).
SELECT
    c->>'$.ServiceComponentInfo.cluster_name' AS cluster_name,
    c->>'$.ServiceComponentInfo.service_name' AS service_name,
    c->>'$.ServiceComponentInfo.component_name' AS component_name,
    c->>'$.ServiceComponentInfo.category' AS category,
    c->>'$.ServiceComponentInfo.display_name' AS display_name,
    c->>'$.ServiceComponentInfo.state' AS state,
    c->>'$.ServiceComponentInfo.desired_stack' AS desired_stack,
    c->>'$.ServiceComponentInfo.desired_version' AS desired_version,
    c->>'$.ServiceComponentInfo.repository_state' AS repository_state,
    CAST(c->>'$.ServiceComponentInfo.total_count' AS BIGINT) AS total_count,
    CAST(c->>'$.ServiceComponentInfo.installed_count' AS BIGINT) AS installed_count,
    CAST(c->>'$.ServiceComponentInfo.started_count' AS BIGINT) AS started_count,
    CAST(c->>'$.ServiceComponentInfo.init_count' AS BIGINT) AS init_count,
    CAST(c->>'$.ServiceComponentInfo.install_failed_count' AS BIGINT) AS install_failed_count,
    CAST(c->>'$.ServiceComponentInfo.installed_and_maintenance_off_count' AS BIGINT) AS installed_and_maintenance_off_count,
    CAST(c->>'$.ServiceComponentInfo.unknown_count' AS BIGINT) AS unknown_count,
    leaf->>'$.metric_path' AS metric_path,
    leaf->>'$.metric_value' AS metric_value
FROM raw_documents,
    UNNEST(hadoop_records(doc, '$.component')) AS t(c),
    UNNEST(
        CASE WHEN json_array_length(doc, '$.metric_leaves') > 0
            THEN hadoop_records(doc, '$.metric_leaves')
            ELSE CAST([NULL] AS JSON[])
        END) AS m(leaf)
