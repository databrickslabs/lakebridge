-- This procedure calculates a per minute breakdown of successful and failed 
-- append requests, split into error code categories

DECLARE metadatalevel STRING DEFAULT 'region-us';
    
-- SET metadatalevel to the format <project>.<region>
SET metadatalevel = 'my-gcp-project.region-us';

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
        `%s`.INFORMATION_SCHEMA.WRITE_API_TIMELINE
    GROUP BY
    start_timestamp
    ORDER BY
    start_timestamp DESC;
""", metadatalevel)
USING 
metadatalevel AS metadatalevel;