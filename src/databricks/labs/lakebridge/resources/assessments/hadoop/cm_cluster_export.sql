-- Cloudera Manager cluster export, flattened: a row per service config, role config group item or role.
-- hostTemplates and instantiator are not extracted.
WITH services AS (
    SELECT s.svc AS svc
    FROM (SELECT unnest(hadoop_records(doc, '$.services')) AS svc FROM raw_documents) AS s
)
SELECT
    'service_config' AS section,
    svc->>'$.refName' AS service_ref,
    svc->>'$.serviceType' AS service_type,
    CAST(NULL AS VARCHAR) AS role_config_group_ref,
    CAST(NULL AS VARCHAR) AS role_type,
    CAST(NULL AS BOOLEAN) AS is_base,
    cfg->>'$.ref' AS ref_name,
    cfg->>'$.name' AS config_name,
    CAST(NULL AS VARCHAR) AS config_value
FROM services, UNNEST(hadoop_records(svc, '$.serviceConfigs')) AS c(cfg)
UNION ALL
SELECT
    'role_config',
    svc->>'$.refName',
    svc->>'$.serviceType',
    grp->>'$.refName',
    grp->>'$.roleType',
    CAST(grp->>'$.base' AS BOOLEAN),
    CAST(NULL AS VARCHAR),
    cfg->>'$.name',
    cfg->>'$.value'
FROM services, UNNEST(hadoop_records(svc, '$.roleConfigGroups')) AS g(grp),
    UNNEST(hadoop_records(grp, '$.configs')) AS k(cfg)
UNION ALL
SELECT
    'role',
    svc->>'$.refName',
    svc->>'$.serviceType',
    CAST(NULL AS VARCHAR),
    role->>'$.roleType',
    CAST(NULL AS BOOLEAN),
    role->>'$.refName',
    CAST(NULL AS VARCHAR),
    CAST(NULL AS VARCHAR)
FROM services, UNNEST(hadoop_records(svc, '$.roles')) AS r(role)
