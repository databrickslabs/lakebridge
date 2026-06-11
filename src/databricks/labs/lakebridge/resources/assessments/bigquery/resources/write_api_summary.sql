-- This procedure calculates a per minute breakdown of successful and failed
-- append requests, split into error code categories

    SELECT
        '{{project_region}}' AS metadata_level,
        start_timestamp,
        SUM(total_requests) AS total_requests,
        SUM(total_rows) AS total_rows,
        SUM(total_input_bytes) AS total_input_bytes,
        SUM(
            IF(
            error_code IN (
                'INVALID_ARGUMENT', 'NOT_FOUND', 'CANCELLED', 'RESOURCE_EXHAUSTED',
                'ALREADY_EXISTS', 'PERMISSION_DENIED', 'UNAUTHENTICATED',
                'FAILED_PRECONDITION', 'OUT_OF_RANGE'),
            total_requests,
            0)) AS user_error,
        SUM(
            IF(
            error_code IN (
                'DEADLINE_EXCEEDED','ABORTED', 'INTERNAL', 'UNAVAILABLE',
                'DATA_LOSS', 'UNKNOWN'),
            total_requests,
            0)) AS server_error,
        SUM(IF(error_code = 'OK', 0, total_requests)) AS total_error,
        FROM
        `{{project_region}}`.INFORMATION_SCHEMA.WRITE_API_TIMELINE
    WHERE start_timestamp >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL {{profiling_window_in_days}} DAY)
    GROUP BY
    start_timestamp
    ORDER BY
    start_timestamp DESC;
