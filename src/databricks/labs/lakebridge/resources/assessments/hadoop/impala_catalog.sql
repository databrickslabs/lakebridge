-- Impala catalogd /catalog: one row per table, with its database. A database without tables has no row.
SELECT
    dbs.coordinator AS coordinator_url,
    dbs.database_name,
    t->>'$.name' AS table_name,
    t->>'$.fqtn' AS fqtn,
    TRY_CAST(t->>'$.has_metrics' AS BOOLEAN) AS has_metrics
FROM (
    -- Unnested in the select list, so the table list is not copied into each row.
    SELECT coordinator, database_name, unnest(tables) AS t
    FROM (
        SELECT coordinator, d->>'$.name' AS database_name, hadoop_records(d, '$.tables') AS tables
        FROM (
            SELECT doc->>'$.coordinator' AS coordinator, unnest(hadoop_records(doc, '$.body.databases')) AS d
            FROM raw_documents
        )
    )
) AS dbs
