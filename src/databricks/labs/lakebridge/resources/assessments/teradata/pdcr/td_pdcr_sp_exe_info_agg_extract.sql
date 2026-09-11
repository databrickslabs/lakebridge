SELECT
    ProcName,
    avg(AMPCPUTime) avgAMPCPUTime,
    avg(ExecutionSecs) avgExecutionSecs,
    avg(NumStatements) NumStatements,
    min(cast(StartTime as date)) as FirstExeDate,
    max(cast(StartTime as date)) as LastExeDate,
    count(*) NumExecutions
FROM
    (SELECT
        A.ProcName,
        B.SessionID,
        B.RequestNum,
        sum(B.AMPCPUTime) AMPCPUTime,
        sum(B.TotalFirstRespTime) ExecutionSecs,
        min(StartTime) as StartTime,
        count(*) as NumStatements

    FROM
        (SELECT SessionID,
            RequestNum,
            QueryText,
            lower(trim(both from oreplace(oTranslate(substr(QueryText, 5, instr(QueryText, '(') -5), '0D0A'XC, ''), '09'XC,' '))) as ProcName
        FROM PDCRINFO.DBQLogTbl_Hst
        WHERE LogDate >= date - :pdcr_lookback_days
            AND upper(trim(both from StatementType)) = 'CALL'
            AND upper(left(oreplace(QueryText, '09'XC,' '), 5)) = 'CALL '
            AND instr(QueryText, '(') > 5
        ) A
    JOIN PDCRINFO.DBQLogTbl_Hst B
    ON  A.SessionID  = B.SessionID
        AND A.RequestNum = B.RequestNum
        AND B.LogDate >= date - :pdcr_lookback_days
        AND B.RequestNum <> B.InternalRequestNum
    GROUP BY 1, 2, 3
    ) X
group by 1;
