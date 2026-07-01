SELECT
    DISTINCT
    NodeID,
    NodeType,
    NCPUs,
    Vproc1,
    MemSize,
    PM_COD_CPU,
    WM_COD_CPU,
    PM_COD_IO,
    WM_COD_IO,
    Tier_factor,
    NodeNormFactor
FROM DBC.ResUsageSpma
WHERE
    TheDate >= date - 30;
