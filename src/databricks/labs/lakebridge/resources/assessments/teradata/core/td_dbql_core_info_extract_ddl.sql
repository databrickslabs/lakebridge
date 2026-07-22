CREATE TABLE td_dbql_core_info_extract (
    AppID VARCHAR,
    UserName VARCHAR,
    SessionID BIGINT,
    SQLTextInfo VARCHAR,
    StartTime TIMESTAMP,
    FirstRespTime TIMESTAMP,
    TotalFirstRespTime DOUBLE,
    TotalCPUTime DOUBLE,
    TotalIOCount DOUBLE,
    ReqPhysIOKB DOUBLE,
    SpoolUsage DOUBLE
);
