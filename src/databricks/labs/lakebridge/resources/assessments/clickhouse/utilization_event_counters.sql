-- Cumulative event counters (system.events).
SELECT event, value, description
FROM system.events
WHERE value > 0
ORDER BY value DESC
LIMIT 100
