-- Ranger: one row per policy item. item_kind is allow, deny, allow_exception, deny_exception, data_mask or row_filter.
-- users, groups, roles, accesses and conditions are JSON text lists. mask_type is set for data_mask items and
-- row_filter_expr for row_filter items.
WITH policies AS (
    SELECT p AS pol FROM (SELECT unnest(hadoop_records(doc, '$')) AS p FROM raw_documents) AS t
),
items AS (
    SELECT pol, 'allow' AS item_kind, it FROM policies, UNNEST(hadoop_records(pol, '$.policyItems')) AS u(it)
    UNION ALL
    SELECT pol, 'deny', it FROM policies, UNNEST(hadoop_records(pol, '$.denyPolicyItems')) AS u(it)
    UNION ALL
    SELECT pol, 'allow_exception', it FROM policies, UNNEST(hadoop_records(pol, '$.allowExceptions')) AS u(it)
    UNION ALL
    SELECT pol, 'deny_exception', it FROM policies, UNNEST(hadoop_records(pol, '$.denyExceptions')) AS u(it)
    UNION ALL
    SELECT pol, 'data_mask', it FROM policies, UNNEST(hadoop_records(pol, '$.dataMaskPolicyItems')) AS u(it)
    UNION ALL
    SELECT pol, 'row_filter', it FROM policies, UNNEST(hadoop_records(pol, '$.rowFilterPolicyItems')) AS u(it)
)
SELECT
    CAST(pol->>'$.id' AS BIGINT) AS policy_id,
    pol->>'$.service' AS service,
    pol->>'$.name' AS policy_name,
    item_kind,
    CAST(it->'$.users' AS VARCHAR) AS users,
    CAST(it->'$.groups' AS VARCHAR) AS groups,
    CAST(it->'$.roles' AS VARCHAR) AS roles,
    CAST(it->'$.accesses' AS VARCHAR) AS accesses,
    CAST(it->>'$.delegateAdmin' AS BOOLEAN) AS delegate_admin,
    CAST(it->'$.conditions' AS VARCHAR) AS conditions,
    it->>'$.dataMaskInfo.dataMaskType' AS mask_type,
    it->>'$.rowFilterInfo.filterExpr' AS row_filter_expr
FROM items
