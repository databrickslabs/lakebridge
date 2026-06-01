-- This procedure summarizes streaming inserts, focusing on request counts, 
-- row counts, input bytes, and specific error conditions.

DECLARE metadatalevel STRING DEFAULT 'region-us';
DECLARE profiling_window_in_days INT64 DEFAULT 180;

-- SET metadatalevel to the format <project>.<region>
SET metadatalevel = 'my-gcp-project.region-us';
SET profiling_window_in_days = 180;

EXECUTE IMMEDIATE
FORMAT("""
    SELECT
        @metadatalevel AS metadata_level,
        start_timestamp,
        SUM(total_requests) AS total_requests,
        SUM(total_rows) AS total_rows,
        SUM(total_input_bytes) AS total_input_bytes,
        SUM(
            IF(
            error_code IN ('QUOTA_EXCEEDED', 'RATE_LIMIT_EXCEEDED'),
            total_requests,
            0)) AS quota_error,
        SUM(
            IF(
            error_code IN (
                'INVALID_VALUE', 'NOT_FOUND', 'SCHEMA_INCOMPATIBLE',
                'BILLING_NOT_ENABLED', 'ACCESS_DENIED', 'UNAUTHENTICATED'),
            total_requests,
            0)) AS user_error,
        SUM(
            IF(
            error_code IN ('CONNECTION_ERROR','INTERNAL_ERROR'),
            total_requests,
            0)) AS server_error,
        SUM(IF(error_code IS NULL, 0, total_requests)) AS total_error
    FROM
    `%s`.INFORMATION_SCHEMA.STREAMING_TIMELINE_BY_PROJECT
    WHERE start_timestamp >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL @profiling_window_in_days DAY)
    GROUP BY
    start_timestamp
    ORDER BY
    start_timestamp DESC;
""", metadatalevel)
USING
metadatalevel AS metadatalevel,
profiling_window_in_days AS profiling_window_in_days;