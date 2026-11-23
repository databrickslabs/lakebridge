-- Settings profiles (system.settings_profiles). Replicated: identical on OSS and Cloud.
-- apply_to_list / apply_to_except are Array(String), JSON-encoded.
SELECT
    name,
    storage,
    num_elements,
    apply_to_all,
    toJSONString(apply_to_list) AS apply_to_list,
    toJSONString(apply_to_except) AS apply_to_except
FROM system.settings_profiles
