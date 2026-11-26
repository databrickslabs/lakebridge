-- Impala statestore /subscribers: one row per subscriber (impalad, catalogd), with seconds since its last heartbeat.
SELECT
    coordinator AS statestore_url,
    s->>'$.id' AS subscriber_id,
    s->>'$.address' AS address,
    TRY_CAST(s->>'$.num_topics' AS BIGINT) AS num_topics,
    TRY_CAST(s->>'$.num_priority_topics' AS BIGINT) AS num_priority_topics,
    TRY_CAST(s->>'$.num_transient' AS BIGINT) AS num_transient,
    s->>'$.registration_id' AS registration_id,
    TRY_CAST(s->>'$.secs_since_heartbeat' AS DOUBLE) AS secs_since_heartbeat
FROM (
    SELECT doc->>'$.coordinator' AS coordinator, unnest(hadoop_records(doc, '$.body.subscribers')) AS s FROM raw_documents
) AS t
