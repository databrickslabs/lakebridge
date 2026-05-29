-- Snowpipe credit usage
-- Background ingestion service. Billed via PIPE_USAGE_HISTORY rather than
-- query_history, so it would otherwise be invisible to the profiler.

SELECT
    PIPE_NAME,
    START_TIME,
    END_TIME,
    CREDITS_USED,
    BYTES_INSERTED,
    FILES_INSERTED,
    CURRENT_TIMESTAMP() as EXTRACT_TIMESTAMP
FROM SNOWFLAKE.ACCOUNT_USAGE.PIPE_USAGE_HISTORY
WHERE START_TIME >= DATEADD('day', -90, CURRENT_TIMESTAMP())
ORDER BY START_TIME DESC;
