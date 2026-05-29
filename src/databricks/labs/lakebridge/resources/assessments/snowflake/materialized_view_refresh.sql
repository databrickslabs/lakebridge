-- Materialized view refresh credit usage
-- Background refresh service. Billed via MATERIALIZED_VIEW_REFRESH_HISTORY,
-- not query_history, so it would otherwise be invisible to the profiler.

SELECT
    DATABASE_NAME,
    SCHEMA_NAME,
    TABLE_NAME,
    START_TIME,
    END_TIME,
    CREDITS_USED,
    CURRENT_TIMESTAMP() as EXTRACT_TIMESTAMP
FROM SNOWFLAKE.ACCOUNT_USAGE.MATERIALIZED_VIEW_REFRESH_HISTORY
WHERE START_TIME >= DATEADD('day', -90, CURRENT_TIMESTAMP())
ORDER BY START_TIME DESC;
