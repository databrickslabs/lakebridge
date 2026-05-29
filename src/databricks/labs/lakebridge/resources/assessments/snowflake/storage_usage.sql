-- Storage Usage and Cost Analysis
-- Extract storage utilization from DATABASE_STORAGE_USAGE_HISTORY
-- Columns: USAGE_DATE, DATABASE_ID, DATABASE_NAME, DELETED,
--   AVERAGE_DATABASE_BYTES, AVERAGE_FAILSAFE_BYTES
--
-- No system-database filter here (unlike database_objects.sql, which excludes
-- SNOWFLAKE / UTIL_DB). This view only reports databases that incur billable
-- storage in the account; the shared SNOWFLAKE database and sample data don't
-- appear, so there's nothing system-owned to filter out. We keep every billed
-- row, including dropped databases still in failsafe, to capture total storage.

SELECT
    DATABASE_NAME,
    USAGE_DATE,
    AVERAGE_DATABASE_BYTES,
    AVERAGE_FAILSAFE_BYTES,
    AVERAGE_DATABASE_BYTES / (1024*1024*1024) as STORAGE_GB,
    AVERAGE_FAILSAFE_BYTES / (1024*1024*1024) as FAILSAFE_GB,
    EXTRACT(month FROM USAGE_DATE) as USAGE_MONTH,
    EXTRACT(year FROM USAGE_DATE) as USAGE_YEAR,
    CURRENT_TIMESTAMP() as EXTRACT_TIMESTAMP
FROM SNOWFLAKE.ACCOUNT_USAGE.DATABASE_STORAGE_USAGE_HISTORY
WHERE USAGE_DATE >= DATEADD('day', -90, CURRENT_DATE())
ORDER BY USAGE_DATE DESC, DATABASE_NAME;
