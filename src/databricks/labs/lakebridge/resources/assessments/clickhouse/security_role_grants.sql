-- Role grants (system.role_grants). Replicated: identical on OSS and Cloud.
SELECT
    user_name,
    role_name,
    granted_role_name,
    granted_role_is_default,
    with_admin_option
FROM system.role_grants
ORDER BY user_name, granted_role_name
