-- Cloudera Manager: one row per configuration item of all hosts (cm/allHosts/config). Values are as CM returns them.
SELECT
    c->>'$.name' AS config_name,
    c->>'$.value' AS config_value,
    CAST(c->>'$.sensitive' AS BOOLEAN) AS sensitive
FROM (SELECT unnest(hadoop_records(doc, '$.items')) AS c FROM raw_documents) AS t
