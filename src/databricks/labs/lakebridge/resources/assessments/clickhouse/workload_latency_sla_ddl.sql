CREATE TABLE IF NOT EXISTS workload_latency_sla (
    query_kind VARCHAR,
    runs UBIGINT,
    p50_ms DOUBLE,
    p75_ms DOUBLE,
    p95_ms DOUBLE,
    p99_ms DOUBLE,
    max_ms UBIGINT
);
