SELECT TOP 100000
    LogTbl.AppID,
    LogTbl.UserName,
    LogTbl.SessionID,
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
WHERE
    LogTbl.CollectTimeStamp >= CURRENT_DATE - INTERVAL '7' DAY
    AND UserName NOT IN ('AD_ANMGR', 'MS_ANMGR')
ORDER BY
    TotalFirstRespTime DESC;
