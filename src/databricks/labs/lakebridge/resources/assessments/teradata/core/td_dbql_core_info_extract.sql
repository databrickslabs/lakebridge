SELECT TOP 100000
    LogTbl.AppID,
    LogTbl.UserName,
    LogTbl.SessionID,
    -- Raw SQL text is redacted at extraction time so query text never leaves the source system.
    '[REDACTED]' AS SQLTextInfo,
    LogTbl.StartTime,
    LogTbl.FirstRespTime,
    LogTbl.TotalFirstRespTime,
    (LogTbl.AMPCPUTime + LogTbl.ParserCPUTime + LogTbl.DisCPUTime) as TotalCPUTime,
    LogTbl.TotalIOCount,
    LogTbl.ReqPhysIOKB,
    LogTbl.SpoolUsage
FROM
    DBC.DBQLogTbl AS LogTbl
JOIN
    DBC.DBQLSQLTbl AS SQLTbl
ON
    LogTbl.ProcID = SQLTbl.ProcID
    AND LogTbl.QueryID = SQLTbl.QueryID
WHERE
    LogTbl.CollectTimeStamp >= CURRENT_DATE - INTERVAL '7' DAY
    AND UserName NOT IN ('AD_ANMGR', 'MS_ANMGR')
ORDER BY
    TotalFirstRespTime DESC;
