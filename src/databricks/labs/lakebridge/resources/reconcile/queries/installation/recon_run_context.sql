CREATE TABLE IF NOT EXISTS recon_run_context (
    recon_id    STRING    NOT NULL,
    config      VARIANT,
    inserted_ts TIMESTAMP NOT NULL
);
