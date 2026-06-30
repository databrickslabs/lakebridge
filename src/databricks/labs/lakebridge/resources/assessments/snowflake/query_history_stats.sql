-- Query History — Aggregate statistics (true totals + weighting basis)
--
-- The full-window aggregate of QUERY_HISTORY with NO query text. Tiny output,
-- but covers every query over the 90-day window (not just the sample), so it
-- carries the *true* totals and per-group distribution the downstream skill
-- applies the sampled category mix against. Also exposes avg/median/p90 so the
-- skill can sense-check the sample and spot outlier-heavy groups.
--
-- Grouped by QUERY_TYPE x WAREHOUSE_SIZE. Credits come from
-- QUERY_ATTRIBUTION_HISTORY, falling back to an execution-time x
-- warehouse-size-rate estimate; ESTIMATED_CREDIT_QUERY_COUNT reports how many
-- rows in each group relied on the fallback.

WITH attributed AS (
    SELECT
        QUERY_ID,
        SUM(CREDITS_ATTRIBUTED_COMPUTE) AS CREDITS_ATTRIBUTED_COMPUTE
    FROM SNOWFLAKE.ACCOUNT_USAGE.QUERY_ATTRIBUTION_HISTORY
    WHERE START_TIME >= DATEADD('day', -90, CURRENT_TIMESTAMP())
    GROUP BY QUERY_ID
),
priced AS (
    SELECT
        qh.QUERY_TYPE,
        qh.WAREHOUSE_SIZE,
        qh.TOTAL_ELAPSED_TIME,
        qh.BYTES_SCANNED,
        COALESCE(
            a.CREDITS_ATTRIBUTED_COMPUTE,
            (qh.EXECUTION_TIME / 3600000.0) * CASE UPPER(qh.WAREHOUSE_SIZE)
                WHEN 'X-SMALL'  THEN 1
                WHEN 'SMALL'    THEN 2
                WHEN 'MEDIUM'   THEN 4
                WHEN 'LARGE'    THEN 8
                WHEN 'X-LARGE'  THEN 16
                WHEN '2X-LARGE' THEN 32
                WHEN '3X-LARGE' THEN 64
                WHEN '4X-LARGE' THEN 128
                WHEN '5X-LARGE' THEN 256
                WHEN '6X-LARGE' THEN 512
                ELSE 0
            END
        ) AS ESTIMATED_CREDITS,
        (a.CREDITS_ATTRIBUTED_COMPUTE IS NULL) AS IS_CREDITS_ESTIMATED
    FROM SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY qh
    LEFT JOIN attributed a ON qh.QUERY_ID = a.QUERY_ID
    WHERE qh.START_TIME >= DATEADD('day', -90, CURRENT_TIMESTAMP())
)
SELECT
    QUERY_TYPE,
    WAREHOUSE_SIZE,
    COUNT(*) AS QUERY_COUNT,
    SUM(CASE WHEN IS_CREDITS_ESTIMATED THEN 1 ELSE 0 END) AS ESTIMATED_CREDIT_QUERY_COUNT,
    SUM(ESTIMATED_CREDITS) AS TOTAL_CREDITS,
    AVG(ESTIMATED_CREDITS) AS AVG_CREDITS,
    MEDIAN(ESTIMATED_CREDITS) AS MEDIAN_CREDITS,
    PERCENTILE_CONT(0.9) WITHIN GROUP (ORDER BY ESTIMATED_CREDITS) AS P90_CREDITS,
    AVG(TOTAL_ELAPSED_TIME) AS AVG_ELAPSED_MS,
    MEDIAN(TOTAL_ELAPSED_TIME) AS MEDIAN_ELAPSED_MS,
    SUM(BYTES_SCANNED) AS TOTAL_BYTES_SCANNED,
    CURRENT_TIMESTAMP() AS EXTRACT_TIMESTAMP
FROM priced
GROUP BY QUERY_TYPE, WAREHOUSE_SIZE
ORDER BY TOTAL_CREDITS DESC NULLS LAST;
