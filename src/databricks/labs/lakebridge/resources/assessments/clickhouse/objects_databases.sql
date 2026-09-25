-- Database inventory (system.databases). Replicated metadata: identical on OSS and Cloud.
SELECT
    name,
    engine,
    data_path,
    metadata_path,
    uuid
FROM system.databases
WHERE name NOT IN ('system', 'information_schema', 'INFORMATION_SCHEMA')
ORDER BY name
