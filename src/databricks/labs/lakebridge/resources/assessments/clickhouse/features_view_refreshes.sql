-- Refreshable MV status (system.view_refreshes). Optional: table only exists on newer builds.
-- Explicit column list (the source SELECT * shape) so the projection matches the DDL. exception
-- redacted (default-on): failure text may echo query literals.
SELECT
    database,
    view,
    uuid,
    status,
    last_success_time,
    last_success_duration_ms,
    last_refresh_time,
    last_refresh_replica,
    next_refresh_time,
    '[REDACTED]' AS exception,
    retry,
    progress,
    read_rows,
    read_bytes,
    total_rows,
    written_rows,
    written_bytes
FROM system.view_refreshes
