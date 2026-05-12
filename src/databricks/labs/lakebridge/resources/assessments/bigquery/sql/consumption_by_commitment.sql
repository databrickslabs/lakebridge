-- Ported from the GCP-native BQ profiler post-analysis notebook
-- (2-data-analysis.py, query_view_consumption_by_commitment). Drops the
-- bq_profiling.<schema>. catalog/schema qualifiers.
--
-- Combines on-demand (beyond commitments) and committed consumption with a
-- `commitment_used` boolean flag so the dashboard can stack them per metadata_level.
CREATE OR REPLACE VIEW consumption_by_commitment AS
SELECT metadata_level,
       edition,
       total_slot_seconds,
       NULL AS commitment_plan,
       FALSE AS commitment_used
FROM consumption_beyond_commitments
UNION ALL
SELECT metadata_level,
       edition,
       total_slot_seconds,
       commitment_plan,
       TRUE AS commitment_used
FROM consumption_through_commitments;
