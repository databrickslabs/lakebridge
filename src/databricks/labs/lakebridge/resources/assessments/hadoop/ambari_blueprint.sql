-- Ambari blueprint: one row per host group and component, with the stack. Cardinality is text, as Ambari returns it;
-- a host group with no components has one row with a NULL component_name.
SELECT
    stack_name,
    stack_version,
    g->>'$.name' AS host_group_name,
    g->>'$.cardinality' AS cardinality,
    c->>'$.name' AS component_name
FROM (
    SELECT doc->>'$.Blueprints.stack_name' AS stack_name, doc->>'$.Blueprints.stack_version' AS stack_version,
        unnest(hadoop_records(doc, '$.host_groups')) AS g
    FROM raw_documents
) AS hg,
    UNNEST(
        CASE WHEN json_array_length(g, '$.components') > 0
            THEN hadoop_records(g, '$.components')
            ELSE CAST([NULL] AS JSON[])
        END) AS cm(c)
