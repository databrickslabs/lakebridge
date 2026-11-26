-- Cloudera Manager: one row per service in the selected cluster.
SELECT
    s->>'$.clusterRef.clusterName' AS cluster_name,
    s->>'$.name' AS service_name,
    s->>'$.displayName' AS display_name,
    s->>'$.type' AS service_type,
    s->>'$.serviceState' AS service_state,
    s->>'$.healthSummary' AS health_summary,
    s->>'$.entityStatus' AS entity_status,
    CAST(s->>'$.maintenanceMode' AS BOOLEAN) AS maintenance_mode,
    s->>'$.configStalenessStatus' AS config_staleness_status,
    CAST(s->'$.healthChecks' AS VARCHAR) AS health_checks,
    s->>'$.serviceVersion' AS service_version
FROM (SELECT unnest(hadoop_records(doc, '$.items')) AS s FROM raw_documents) AS t
