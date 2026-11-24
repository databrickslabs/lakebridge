-- Cloudera Manager: long-format timeseries, one row per sample. Rows with status 'unsupported_by_cm' name a metric the CM version rejected; they carry no sample.
WITH queries AS (
    SELECT doc->>'$.query_name' AS query_name, doc AS body
    FROM raw_documents
),
series AS (
    SELECT q.query_name, s.ts AS ts
    FROM queries q,
        UNNEST(hadoop_records(q.body, '$.response.items')) AS i(item),
        UNNEST(hadoop_records(i.item, '$.timeSeries')) AS s(ts)
),
samples AS (
    SELECT
        se.query_name AS query_name,
        'returned' AS status,
        se.ts->>'$.metadata.metricName' AS metric_name,
        se.ts->>'$.metadata.entityName' AS entity_name,
        se.ts->>'$.metadata.attributes.clusterName' AS cluster_name,
        CAST(se.ts->'$.metadata.attributes' AS VARCHAR) AS entity_attributes,
        CAST(p.pt->>'$.timestamp' AS TIMESTAMP) AS sample_time,
        CAST(p.pt->>'$.value' AS DOUBLE) AS sample_value,
        se.ts->>'$.metadata.rollupUsed' AS rollup,
        se.ts->>'$.metadata.unitNumerators[0]' AS unit
    FROM series se, UNNEST(hadoop_records(se.ts, '$.data')) AS p(pt)
),
dropped AS (
    SELECT
        q.query_name AS query_name,
        'unsupported_by_cm' AS status,
        json_extract_string(m.metric, '$') AS metric_name,
        CAST(NULL AS VARCHAR) AS entity_name,
        CAST(NULL AS VARCHAR) AS cluster_name,
        CAST(NULL AS VARCHAR) AS entity_attributes,
        CAST(NULL AS TIMESTAMP) AS sample_time,
        CAST(NULL AS DOUBLE) AS sample_value,
        CAST(NULL AS VARCHAR) AS rollup,
        CAST(NULL AS VARCHAR) AS unit
    FROM queries q, UNNEST(hadoop_records(q.body, '$.unsupported_metrics')) AS m(metric)
)
SELECT * FROM samples
UNION ALL
SELECT * FROM dropped
