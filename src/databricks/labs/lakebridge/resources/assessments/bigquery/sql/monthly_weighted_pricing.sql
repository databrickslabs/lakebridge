-- Ported from the GCP-native BQ profiler post-analysis notebook
-- (2-data-analysis.py, query_mthly_wght_pricing). Drops the
-- bq_profiling.<schema>. catalog/schema qualifiers; otherwise unchanged.
CREATE OR REPLACE TABLE monthly_weighted_pricing AS
SELECT
    metadata_level,
    month_window,
    price_percentile,
    SUM(db_cost) AS db_cost,
    SUM(vm_cost) AS vm_cost
FROM (
    SELECT metadata_level, month_window, 'db_price_avg' AS price_percentile,
           sum(db_price_avg) AS db_cost, sum(vm_price_avg) AS vm_cost
    FROM bq_slots_pricing_analysis
    GROUP BY month_window, metadata_level
    UNION ALL
    SELECT metadata_level, month_window, 'db_price_50th' AS price_percentile,
           sum(db_price_50th) AS db_cost, sum(vm_price_50th) AS vm_cost
    FROM bq_slots_pricing_analysis
    GROUP BY month_window, metadata_level
    UNION ALL
    SELECT metadata_level, month_window, 'db_price_90th' AS price_percentile,
           sum(db_price_90th) AS db_cost, sum(vm_price_90th) AS vm_cost
    FROM bq_slots_pricing_analysis
    GROUP BY month_window, metadata_level
    UNION ALL
    SELECT metadata_level, month_window, 'db_price_99th' AS price_percentile,
           sum(db_price_99th) AS db_cost, sum(vm_price_99th) AS vm_cost
    FROM bq_slots_pricing_analysis
    GROUP BY month_window, metadata_level
    UNION ALL
    SELECT metadata_level, month_window, 'db_price_max' AS price_percentile,
           sum(db_price_max) AS db_cost, sum(vm_price_max) AS vm_cost
    FROM bq_slots_pricing_analysis
    GROUP BY month_window, metadata_level
    UNION ALL
    SELECT metadata_level, month_window, 'db_price_perf_based' AS price_percentile,
           sum(db_price_perf_based) AS db_cost, 0.00 AS vm_cost
    FROM bq_slots_pricing_analysis
    GROUP BY month_window, metadata_level
) a
GROUP BY metadata_level, month_window, price_percentile
ORDER BY metadata_level, month_window, price_percentile;
