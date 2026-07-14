-- Query Samples — bounded RANDOM sample WITH QUERY_TEXT.
--
-- Full query_history omits SQL text to keep extracts shareable and fast.
-- This step returns a flat 10k-row sample so downstream heuristics still have text to inspect.
-- Join to query_history on QUERY_ID for the full metric row.

SELECT
    QUERY_ID,
    QUERY_TEXT,
    QUERY_TYPE,
    USER_NAME,
    WAREHOUSE_NAME,
    WAREHOUSE_SIZE,
    START_TIME,
    TOTAL_ELAPSED_TIME,
    CURRENT_TIMESTAMP() as EXTRACT_TIMESTAMP
FROM SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY
WHERE START_TIME >= DATEADD('day', -90, CURRENT_TIMESTAMP())
ORDER BY RANDOM()
LIMIT 10000;
