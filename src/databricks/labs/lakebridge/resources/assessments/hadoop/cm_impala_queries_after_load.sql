-- One row per Impala query. A query spanning windows appears in each; keep a finished copy (FINISHED or
-- EXCEPTION), else the longest-running one. Every copy stays in hadoop_raw_responses.
DELETE FROM cm_impala_queries
WHERE rowid IN (
    SELECT rowid
    FROM (
        SELECT
            rowid,
            ROW_NUMBER() OVER (
                PARTITION BY query_id
                ORDER BY
                    CASE WHEN query_state IN ('FINISHED', 'EXCEPTION') THEN 0 ELSE 1 END,
                    duration_ms DESC NULLS LAST,
                    end_time DESC NULLS LAST
            ) AS copy
        FROM cm_impala_queries
        WHERE query_id IS NOT NULL
    )
    WHERE copy > 1
)
