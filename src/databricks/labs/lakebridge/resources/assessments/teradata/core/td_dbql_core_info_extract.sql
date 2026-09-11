SELECT
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
    LogTbl.CollectTimeStamp >= CURRENT_DATE - :lookback_days
    AND UserName NOT IN ('AD_ANMGR', 'MS_ANMGR')
QUALIFY ROW_NUMBER() OVER (ORDER BY TotalFirstRespTime DESC) <= :max_rows
ORDER BY
    TotalFirstRespTime DESC;
