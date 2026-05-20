SELECT
    session_id,
    status,
    request_id,
    security_id,
    login_name,
    login_time,
    query_count,
    is_transactional,
    client_id,
    app_name,
    sql_spid,
    CURRENT_TIMESTAMP AS extract_ts
FROM sys.dm_pdw_exec_sessions
WHERE CHARINDEX('system', LOWER(login_name)) = 0
