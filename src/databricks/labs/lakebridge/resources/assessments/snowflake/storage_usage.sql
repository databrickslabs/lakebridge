-- Storage Usage and Cost Analysis
-- Extract storage utilization from DATABASE_STORAGE_USAGE_HISTORY
-- Columns: USAGE_DATE, DATABASE_ID, DATABASE_NAME, DELETED,
--   AVERAGE_DATABASE_BYTES, AVERAGE_FAILSAFE_BYTES
--
-- NOTE: intentionally no NOT IN ('SNOWFLAKE', 'UTIL_DB') filter here, even
-- though database_objects.sql filters those out. DATABASE_STORAGE_USAGE_HISTORY
-- only includes billed databases, so the shared SNOWFLAKE database never appears,
-- and UTIL_DB only shows up in legacy accounts where it's a real customer
-- database. Applying the filter would zero out storage for those accounts, so we
-- keep every billed row (including dropped databases still in failsafe).

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
