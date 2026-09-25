-- Capability inventory of available engines (system.table_engines). Replicated: identical on OSS and Cloud.
SELECT
    name,
    supports_settings,
    supports_skipping_indices,
    supports_projections,
    supports_sort_order,
    supports_replication
FROM system.table_engines
ORDER BY name
