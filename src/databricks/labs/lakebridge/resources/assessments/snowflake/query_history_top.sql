-- Query History — Top-N most expensive queries (outlier protection)
--
-- The random sample in query_history.sql gives a representative workload mix but,
-- by design, can miss the handful of very expensive queries that drive a large
-- share of spend. This step captures the top 20 queries by credits (with
-- QUERY_TEXT) so those outliers are always present for the downstream skill to
-- sense-check the sampled distribution against.
--
-- Credits come from QUERY_ATTRIBUTION_HISTORY (accurate compute attribution),
-- falling back to an execution-time x warehouse-size-rate estimate where the
-- attribution view has no row (IS_CREDITS_ESTIMATED flags those).

WITH attributed AS (
    SELECT
        QUERY_ID,
        SUM(CREDITS_ATTRIBUTED_COMPUTE) AS CREDITS_ATTRIBUTED_COMPUTE
    FROM SNOWFLAKE.ACCOUNT_USAGE.QUERY_ATTRIBUTION_HISTORY
    WHERE START_TIME >= DATEADD('day', -90, CURRENT_TIMESTAMP())
    GROUP BY QUERY_ID
)
SELECT
    qh.QUERY_ID,
    qh.QUERY_TEXT,
    qh.QUERY_TYPE,
    qh.DATABASE_NAME,
    qh.SCHEMA_NAME,
    qh.USER_NAME,
    qh.WAREHOUSE_NAME,
    qh.WAREHOUSE_SIZE,
    qh.START_TIME,
    qh.TOTAL_ELAPSED_TIME,
    qh.EXECUTION_TIME,
    qh.BYTES_SCANNED,
    qh.ROWS_PRODUCED,
    a.CREDITS_ATTRIBUTED_COMPUTE,
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
    (a.CREDITS_ATTRIBUTED_COMPUTE IS NULL) AS IS_CREDITS_ESTIMATED,
    CURRENT_TIMESTAMP() AS EXTRACT_TIMESTAMP
FROM SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY qh
LEFT JOIN attributed a ON qh.QUERY_ID = a.QUERY_ID
WHERE qh.START_TIME >= DATEADD('day', -90, CURRENT_TIMESTAMP())
ORDER BY ESTIMATED_CREDITS DESC NULLS LAST
LIMIT 20;
