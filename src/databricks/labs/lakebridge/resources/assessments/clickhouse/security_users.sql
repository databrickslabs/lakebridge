-- Users (system.users). Replicated: identical on OSS and Cloud.
-- auth_params and host_* redacted (default-on): auth_params may carry password hashes/tokens;
-- host_* reveal network topology. auth_type is an enum Array; the redacted host_* and list columns
-- are Array(String) so JSON-encoding is unnecessary for the redacted literals. The non-redacted
-- *_list / *_except Array(String) columns are JSON-encoded to a single column.
SELECT
    name,
    storage,
    auth_type,
    '[REDACTED]' AS auth_params,
    '[REDACTED]' AS host_ip,
    '[REDACTED]' AS host_names,
    '[REDACTED]' AS host_names_regexp,
    '[REDACTED]' AS host_names_like,
    default_roles_all,
    toJSONString(default_roles_list) AS default_roles_list,
    toJSONString(default_roles_except) AS default_roles_except,
    grantees_any,
    toJSONString(grantees_list) AS grantees_list,
    toJSONString(grantees_except) AS grantees_except,
    default_database
FROM system.users
ORDER BY name
