CREATE TABLE td_pdcr_info_agg_extract (
    QueryType VARCHAR,
    WorkloadBucket VARCHAR,
    QryCNT BIGINT,
    SumCPU DOUBLE,
    AvgCPU DOUBLE,
    MaxCPU DOUBLE,
    SumIO DOUBLE,
    AvgIO DOUBLE,
    MaxIO DOUBLE,
    MaxTDWMDelayTime DOUBLE,
    SumTDWMDelayTime DOUBLE,
    AvgRespSecs DOUBLE,
    MaxRespSecs DOUBLE
);
