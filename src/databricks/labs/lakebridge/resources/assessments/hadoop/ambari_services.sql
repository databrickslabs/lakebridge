-- Ambari services in the cluster (names only, as the original's services call returns them).
SELECT
    s->>'$.ServiceInfo.cluster_name' AS cluster_name,
    s->>'$.ServiceInfo.service_name' AS service_name
FROM (SELECT unnest(hadoop_records(doc, '$.items')) AS s FROM raw_documents) AS t
