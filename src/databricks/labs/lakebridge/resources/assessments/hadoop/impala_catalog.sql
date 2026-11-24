-- Impala catalogd web UI /catalog: one row per table the catalog knows about, with the database it is in.
-- A database with no tables has no row.
SELECT
    doc->>'$.coordinator' AS coordinator_url,
    d->>'$.name' AS database_name,
    t->>'$.name' AS table_name,
    t->>'$.fqtn' AS fqtn,
    TRY_CAST(t->>'$.has_metrics' AS BOOLEAN) AS has_metrics
FROM raw_documents,
    UNNEST(hadoop_records(doc, '$.body.databases')) AS dbs(d),
    UNNEST(hadoop_records(d, '$.tables')) AS tbls(t)
