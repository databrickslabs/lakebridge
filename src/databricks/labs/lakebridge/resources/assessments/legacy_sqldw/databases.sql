SELECT
    db.name AS database_name,
    ds.edition,
    ds.service_objective,
    CURRENT_TIMESTAMP AS extract_ts
FROM sys.database_service_objectives AS ds
JOIN sys.databases AS db
ON ds.database_id = db.database_id
