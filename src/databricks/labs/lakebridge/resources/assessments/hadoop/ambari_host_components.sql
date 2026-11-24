-- Ambari host components: one row per component installed on each host.
SELECT
    hc->>'$.HostRoles.cluster_name' AS cluster_name,
    hc->>'$.HostRoles.host_name' AS host_name,
    hc->>'$.HostRoles.component_name' AS component_name
FROM raw_documents,
    UNNEST(hadoop_records(doc, '$.items')) AS t(h),
    UNNEST(hadoop_records(h, '$.host_components')) AS hcs(hc)
