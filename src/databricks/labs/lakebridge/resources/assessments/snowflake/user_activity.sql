-- User Activity — per-user summary (was a raw LOGIN_HISTORY dump)
--
-- Issue #2532 (secondary bloat): the previous step dumped one row per login event
-- from LOGIN_HISTORY. That detail isn't used downstream; the model only needs the
-- shape of user activity. This step now returns one summary row per user x client
-- type — login counts, success/failure, distinct source IPs, active days, and the
-- first/last login window — keeping the table small on busy accounts.

SELECT
    USER_NAME,
    REPORTED_CLIENT_TYPE,
    COUNT(*) AS LOGIN_COUNT,
    SUM(CASE WHEN IS_SUCCESS = 'YES' THEN 1 ELSE 0 END) AS SUCCESS_COUNT,
    SUM(CASE WHEN IS_SUCCESS = 'NO' THEN 1 ELSE 0 END) AS FAILURE_COUNT,
    COUNT(DISTINCT CLIENT_IP) AS DISTINCT_CLIENT_IPS,
    COUNT(DISTINCT DATE(EVENT_TIMESTAMP)) AS ACTIVE_DAYS,
    MIN(EVENT_TIMESTAMP) AS FIRST_LOGIN,
    MAX(EVENT_TIMESTAMP) AS LAST_LOGIN,
    CURRENT_TIMESTAMP() AS EXTRACT_TIMESTAMP
FROM SNOWFLAKE.ACCOUNT_USAGE.LOGIN_HISTORY
WHERE EVENT_TIMESTAMP >= DATEADD('day', -90, CURRENT_TIMESTAMP())
    AND USER_NAME IS NOT NULL
GROUP BY USER_NAME, REPORTED_CLIENT_TYPE
ORDER BY LOGIN_COUNT DESC;
