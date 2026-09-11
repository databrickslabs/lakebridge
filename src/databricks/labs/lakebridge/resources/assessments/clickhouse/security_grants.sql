-- Privilege grants (system.grants). Replicated: identical on OSS and Cloud.
SELECT
    user_name,
    role_name,
    access_type,
    database,
    table,
    column,
    is_partial_revoke,
    grant_option
FROM system.grants
ORDER BY user_name, role_name, database, table
