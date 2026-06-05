CREATE OR REPLACE VIEW column_metrics AS
WITH exploded AS (
    SELECT
        d.recon_table_id,
        d.inserted_ts,
        explode(d.data) AS row_data
    FROM details d
    WHERE d.recon_type = 'mismatch'
)
SELECT
    e.recon_table_id,
    e.inserted_ts,
    REGEXP_REPLACE(map_key, '_match$', '') AS column_name,
    SUM(CASE WHEN map_value = 'false' THEN 1 ELSE 0 END) AS mismatch_count
FROM exploded e
LATERAL VIEW explode(row_data) kvs AS map_key, map_value
WHERE map_key LIKE '%_match'
GROUP BY e.recon_table_id, e.inserted_ts, REGEXP_REPLACE(map_key, '_match$', '')
;
