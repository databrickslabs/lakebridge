-- Login/session activity (session_log). Optional: session_log is absent on many OSS builds.
SELECT
    type,
    user,
    auth_type,
    count() AS event_count,
    uniqExact(client_hostname) AS distinct_hosts,
    min(event_time) AS first_seen,
    max(event_time) AS last_seen
FROM system.session_log
WHERE event_time >= now() - INTERVAL 30 DAY
GROUP BY type, user, auth_type
ORDER BY event_count DESC
