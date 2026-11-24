-- Cloudera Manager: long-format timeseries, one row per sample. Rows with status 'unsupported_by_cm' name a metric the CM version rejected; they carry no sample.
-- Each level is unnested in the select list, and a series' own columns are computed (MATERIALIZED) before its points
-- are unnested, so no row carries a whole response or a series' data list: on a large cluster one response holds
-- hundreds of thousands of points.
WITH items AS (
    SELECT doc->>'$.query_name' AS query_name, unnest(hadoop_records(doc, '$.response.items')) AS item
    FROM raw_documents
),
series AS (
    SELECT query_name, unnest(hadoop_records(item, '$.timeSeries')) AS ts
    FROM items
),
series_columns AS MATERIALIZED (
    SELECT
        query_name,
        ts->>'$.metadata.metricName' AS metric_name,
        ts->>'$.metadata.entityName' AS entity_name,
        ts->>'$.metadata.attributes.clusterName' AS cluster_name,
        CAST(ts->'$.metadata.attributes' AS VARCHAR) AS entity_attributes,
        ts->>'$.metadata.rollupUsed' AS rollup,
        ts->>'$.metadata.unitNumerators[0]' AS unit,
        hadoop_records(ts, '$.data') AS points
    FROM series
),
points AS (
    SELECT query_name, metric_name, entity_name, cluster_name, entity_attributes, rollup, unit, unnest(points) AS pt
    FROM series_columns
),
samples AS (
    SELECT
        se.query_name AS query_name,
        'returned' AS status,
        se.metric_name,
        se.entity_name,
        se.cluster_name,
        se.entity_attributes,
        CAST(se.pt->>'$.timestamp' AS TIMESTAMP) AS sample_time,
        CAST(se.pt->>'$.value' AS DOUBLE) AS sample_value,
        se.rollup,
        se.unit,
        -- An hourly rollup point summarises its samples: count, min, max, mean and standard deviation, and for a
        -- cross-entity metric (..._across_hosts) the entities with the lowest and highest values.
        CAST(se.pt->>'$.type' AS VARCHAR) AS point_type,
        CAST(se.pt->>'$.aggregateStatistics.count' AS BIGINT) AS aggregate_count,
        CAST(se.pt->>'$.aggregateStatistics.min' AS DOUBLE) AS aggregate_min,
        CAST(se.pt->>'$.aggregateStatistics.minTime' AS TIMESTAMP) AS aggregate_min_time,
        CAST(se.pt->>'$.aggregateStatistics.max' AS DOUBLE) AS aggregate_max,
        CAST(se.pt->>'$.aggregateStatistics.maxTime' AS TIMESTAMP) AS aggregate_max_time,
        CAST(se.pt->>'$.aggregateStatistics.mean' AS DOUBLE) AS aggregate_mean,
        CAST(se.pt->>'$.aggregateStatistics.stdDev' AS DOUBLE) AS aggregate_std_dev,
        CAST(se.pt->>'$.aggregateStatistics.crossEntityMetadata.numEntities' AS DOUBLE) AS cross_entity_count,
        CAST(se.pt->>'$.aggregateStatistics.crossEntityMetadata.minEntityName' AS VARCHAR) AS cross_entity_min_name,
        CAST(se.pt->>'$.aggregateStatistics.crossEntityMetadata.maxEntityName' AS VARCHAR) AS cross_entity_max_name
    FROM points se
),
dropped AS (
    SELECT
        m.query_name AS query_name,
        'unsupported_by_cm' AS status,
        json_extract_string(m.metric, '$') AS metric_name,
        CAST(NULL AS VARCHAR) AS entity_name,
        CAST(NULL AS VARCHAR) AS cluster_name,
        CAST(NULL AS VARCHAR) AS entity_attributes,
        CAST(NULL AS TIMESTAMP) AS sample_time,
        CAST(NULL AS DOUBLE) AS sample_value,
        CAST(NULL AS VARCHAR) AS rollup,
        CAST(NULL AS VARCHAR) AS unit,
        CAST(NULL AS VARCHAR) AS point_type,
        CAST(NULL AS BIGINT) AS aggregate_count,
        CAST(NULL AS DOUBLE) AS aggregate_min,
        CAST(NULL AS TIMESTAMP) AS aggregate_min_time,
        CAST(NULL AS DOUBLE) AS aggregate_max,
        CAST(NULL AS TIMESTAMP) AS aggregate_max_time,
        CAST(NULL AS DOUBLE) AS aggregate_mean,
        CAST(NULL AS DOUBLE) AS aggregate_std_dev,
        CAST(NULL AS DOUBLE) AS cross_entity_count,
        CAST(NULL AS VARCHAR) AS cross_entity_min_name,
        CAST(NULL AS VARCHAR) AS cross_entity_max_name
    FROM (
        SELECT doc->>'$.query_name' AS query_name, unnest(hadoop_records(doc, '$.unsupported_metrics')) AS metric
        FROM raw_documents
    ) AS m
)
SELECT * FROM samples
UNION ALL
SELECT * FROM dropped
