-- Asynchronous insert attribution (asynchronous_insert_log). Optional: table may be disabled/absent.
SELECT
    database,
    table,
    sum(bytes) AS async_inserted_bytes,
    sum(rows) AS async_inserted_rows,
    count() AS async_insert_batches
FROM system.asynchronous_insert_log
WHERE event_time >= now() - INTERVAL 30 DAY
  AND status = 'Ok'
GROUP BY database, table
ORDER BY async_inserted_bytes DESC
