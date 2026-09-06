-- Static pricing config for self-managed / OSS: no Cloud metered pricing applies, so no
-- dollar figures. Cloud populates this table instead via the optional cost_enrich python step.
SELECT
    NULL AS region_detected,
    'not_applicable' AS region_source,
    NULL AS tier,
    'unknown' AS tier_source,
    false AS is_cloud,
    'Self-managed ClickHouse detected - ClickHouse Cloud metered pricing does not apply (cost is your own VM/bare-metal + disk infrastructure), so no dollar figures are produced. The resource footprint and all usage attribution are still reported.' AS note,
    NULL AS cloud_service,
    NULL AS actual_billed_cost
