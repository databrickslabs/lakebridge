CREATE TABLE rs_serverless_usage (
    day                  DATE,
    rpu_capacity         BIGINT,
    compute_seconds      DOUBLE,
    charged_seconds      DOUBLE,
    rpu_hours            DOUBLE,
    legacy_cost_incurred DOUBLE
);
