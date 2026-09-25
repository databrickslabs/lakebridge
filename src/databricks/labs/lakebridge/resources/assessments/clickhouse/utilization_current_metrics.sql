-- Current server metrics (system.metrics).
SELECT metric, value, description
FROM system.metrics
WHERE value != 0
ORDER BY value DESC
