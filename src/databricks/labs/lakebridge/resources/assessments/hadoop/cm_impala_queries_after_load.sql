-- One row per Impala query. A query that runs across several time windows is listed in each of them, each copy as it
-- was at that moment. Keep the most complete copy: a finished one (FINISHED or EXCEPTION) if there is one, otherwise the
-- one with the longest duration, which is the latest view. Every copy stays in hadoop_raw_responses, as CM returned it.
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
