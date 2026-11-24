-- Ambari blueprint configurations: one row per property of each config type, value kept as Ambari returns it (text).
-- Each configuration object holds one config type; its properties are read with the property names as JSON keys.
SELECT
    t.config_type,
    p.property,
    json_extract_string(t.properties, '$."' || p.property || '"') AS property_value
FROM (
    SELECT
        k AS config_type,
        json_extract(cfg, '$."' || k || '".properties') AS properties
    FROM (SELECT unnest(hadoop_records(doc, '$.configurations')) AS cfg FROM raw_documents) AS cf,
        UNNEST(json_keys(cfg)) AS ck(k)
) AS t,
    UNNEST(json_keys(t.properties)) AS p(property)
