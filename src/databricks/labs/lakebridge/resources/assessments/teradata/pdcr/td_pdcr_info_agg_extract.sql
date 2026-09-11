SELECT
    QueryType,
    CASE
        WHEN QueryType = 'ETL' THEN 'ETL'
        WHEN QueryType = 'BI/QUERY' THEN 'BI'
        ELSE 'INTERACTIVE'
    END AS WorkloadBucket,
    COUNT(*) AS QryCNT,
    SUM(AMPCPUTime) AS SumCPU,
    AVG(AMPCPUTime) AS AvgCPU,
    MAX(AMPCPUTime) AS MaxCPU,
    SUM(TotalIOCount) AS SumIO,
    AVG(TotalIOCount) AS AvgIO,
    MAX(TotalIOCount) AS MaxIO,
    MAX(DelayTime) AS MaxTDWMDelayTime,
    SUM(DelayTime) AS SumTDWMDelayTime,
    AVG(ResponseSecs) AS AvgRespSecs,
    MAX(ResponseSecs) AS MaxRespSecs
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
        END AS QueryType,
        AMPCPUTime,
        TotalIOCount,
        DelayTime,
        ZeroIfNull(
            CAST(
                EXTRACT(HOUR FROM ((FirstRespTime - StartTime) DAY(4) TO SECOND(6))) * 3600
                + EXTRACT(MINUTE FROM ((FirstRespTime - StartTime) DAY(4) TO SECOND(6))) * 60
                + EXTRACT(SECOND FROM ((FirstRespTime - StartTime) DAY(4) TO SECOND(6)))
                AS DECIMAL(10, 2)
            )
        ) AS ResponseSecs
    FROM PDCRINFO.DBQLogTbl_Hst
    WHERE (AMPCPUTime > 0 OR TotalIOCount > 0)
        AND NumSteps > 0
        AND LogDate >= DATE - :pdcr_lookback_days
) AS ClassifiedQueries
GROUP BY 1, 2;
