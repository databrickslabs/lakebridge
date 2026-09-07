SELECT
    180 AS LookbackDays,
    COUNT(*) AS BIQueryCount,
    CAST(COUNT(*) AS FLOAT) / (180 * 24 * 60) AS AvgBIQueriesPerMinute
FROM (
    SELECT
        CASE
            WHEN INSTR(UPPER(StatementGroup), 'DDL CREATE') > 0
                AND INSTR(UPPER(QueryText), 'SELECT') > 0 THEN 'ETL'
            WHEN INSTR(UPPER(StatementGroup), 'DML') > 0
                AND INSTR(UPPER(StatementGroup), 'INSERT') > 0 THEN 'ETL'
            WHEN INSTR(UPPER(StatementGroup), 'DML') > 0
                AND INSTR(UPPER(StatementGroup), 'UPDATE') > 0 THEN 'ETL'
            WHEN INSTR(UPPER(StatementGroup), 'DML') > 0
                AND INSTR(UPPER(StatementGroup), 'DELETE') > 0 THEN 'ETL'
            WHEN INSTR(UPPER(StatementGroup), 'DML') > 0
                AND INSTR(UPPER(StatementGroup), 'DEL=') > 0
                AND INSTR(UPPER(StatementGroup), 'DEL=0') = 0 THEN 'ETL'
            WHEN INSTR(UPPER(StatementGroup), 'DML') > 0
                AND INSTR(UPPER(StatementGroup), 'INS=') > 0
                AND INSTR(UPPER(StatementGroup), 'INS=0') = 0 THEN 'ETL'
            WHEN INSTR(UPPER(StatementGroup), 'DML') > 0
                AND INSTR(UPPER(StatementGroup), 'INSSEL=') > 0
                AND INSTR(UPPER(StatementGroup), 'INSSEL=0') = 0 THEN 'ETL'
            WHEN INSTR(UPPER(StatementGroup), 'DML') > 0
                AND INSTR(UPPER(StatementGroup), 'UPD=') > 0
                AND INSTR(UPPER(StatementGroup), 'UPD=0') = 0 THEN 'ETL'
            WHEN UPPER(AppId) IN ('FASTEXP', 'MULTLOAD', 'FASTLOAD') THEN 'ETL'
            WHEN INSTR(UPPER(StatementGroup), 'SELECT') > 0
                OR INSTR(UPPER(QueryText), 'SELECT') = 1 THEN 'BI/QUERY'
            WHEN INSTR(UPPER(StatementGroup), 'DDL') > 0 THEN 'DDL'
            WHEN INSTR(UPPER(StatementType), 'PROCEDURE') > 0
                AND (
                    INSTR(UPPER(StatementType), 'CREATE') > 0
                    OR INSTR(UPPER(StatementType), 'REPLACE') > 0
                ) THEN 'DML'
            WHEN INSTR(UPPER(StatementType), 'CALL') > 0 THEN 'SP'
            ELSE 'OTHER'
        END AS QueryType
    FROM PDCRINFO.DBQLogTbl_Hst
    WHERE (AMPCPUTime > 0 OR TotalIOCount > 0)
        AND NumSteps > 0
        AND LogDate >= DATE - 180
) AS ClassifiedQueries
WHERE QueryType = 'BI/QUERY';
