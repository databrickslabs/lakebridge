-- Ranger: one row per policy. policy_type maps 0 to access, 1 to masking and 2 to row_filter. The policy's items are in
-- ranger_policy_items; resources, conditions and validity schedules are kept as JSON text.
SELECT
    CAST(p->>'$.id' AS BIGINT) AS id,
    p->>'$.guid' AS guid,
    p->>'$.service' AS service,
    p->>'$.serviceType' AS service_type,
    p->>'$.name' AS name,
    p->>'$.description' AS description,
    COALESCE(CAST(p->>'$.policyType' AS BIGINT), 0) AS policy_type_code,
    CASE COALESCE(CAST(p->>'$.policyType' AS BIGINT), 0)
        WHEN 0 THEN 'access'
        WHEN 1 THEN 'masking'
        WHEN 2 THEN 'row_filter'
        ELSE 'unknown'
    END AS policy_type,
    CAST(p->>'$.policyPriority' AS BIGINT) AS priority,
    CAST(p->>'$.isEnabled' AS BOOLEAN) AS is_enabled,
    CAST(p->>'$.isAuditEnabled' AS BOOLEAN) AS is_audit_enabled,
    CAST(p->>'$.isDenyAllElse' AS BOOLEAN) AS is_deny_all_else,
    CAST(p->>'$.version' AS BIGINT) AS version,
    CAST(p->'$.resources' AS VARCHAR) AS resources,
    CAST(p->'$.conditions' AS VARCHAR) AS conditions,
    CAST(p->'$.validitySchedules' AS VARCHAR) AS validity_schedules,
    CAST(p->'$.policyLabels' AS VARCHAR) AS policy_labels
FROM raw_documents, UNNEST(hadoop_records(doc, '$')) AS t(p)
