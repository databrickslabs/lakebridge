SELECT
    db.name AS database_name,
    CURRENT_TIMESTAMP AS extract_ts
FROM sys.databases AS db;
