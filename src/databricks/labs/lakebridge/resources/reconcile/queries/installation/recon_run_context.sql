CREATE TABLE IF NOT EXISTS recon_run_context (
    recon_id    STRING    NOT NULL COMMENT 'Reconcile run id. Join key to main.',
    config      VARIANT COMMENT 'Full run config (reconcile settings + table config) = the run intent.',
    inserted_ts TIMESTAMP NOT NULL COMMENT 'Row insert timestamp.'
)
COMMENT 'The config/intent for each run. One row per recon_id. Join to main on recon_id.';
