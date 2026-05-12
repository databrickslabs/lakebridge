-- Ported from the GCP-native BQ profiler post-analysis notebook
-- (2-data-analysis.py, query_pricing_analysis). DuckDB adaptations:
--   * Drop bq_profiling.<schema>. catalog/schema qualifiers — we run this in-process
--     against a local DuckDB; tables live in the default schema.
--   * Replace Spark date_format(x, "yyyy-MM-dd") with substring() on the upstream
--     'YYYY-MM-DDTHH' string format that timeline_analysis.sql emits.
--   * {{var}} placeholders are Python-style and substituted at runtime via
--     bq_pricing_analysis.py using TUNING_INPUT_PARAMS[target_cloud].
CREATE OR REPLACE TABLE bq_slots_pricing_analysis AS
SELECT
    substring(time_window, 1, 4) AS year_window,
    substring(time_window, 1, 7) AS month_window,
    substring(time_window, 1, 10) AS date_window,
    metadata_level,
    CASE WHEN workload_type NOT IN ('BI', 'ETL') OR workload_type IS NULL THEN 'ETL'
         ELSE workload_type END AS workload_type,
    slot_secs,
    slots_avg,
    slots_perc_50th,
    slots_perc_90th,
    slots_perc_99th,
    slots_max,
    cumulative_secs_spent_in_exec,
    (slots_avg / {db_cores_to_bq_slots_ratio}) AS db_cores_avg,
    (slots_perc_50th / {db_cores_to_bq_slots_ratio}) AS db_cores_50th,
    (slots_perc_90th / {db_cores_to_bq_slots_ratio}) AS db_cores_90th,
    (slots_perc_99th / {db_cores_to_bq_slots_ratio}) AS db_cores_99th,
    (slots_max / {db_cores_to_bq_slots_ratio}) AS db_cores_max,
    CASE
        WHEN workload_type = 'BI' THEN (db_cores_avg / b.worker_cpu_count)
        ELSE (db_cores_avg / ({db_etl_cores_per_executor} * {db_etl_executors_per_cluster}))
    END AS db_num_clusters_avg,
    CASE
        WHEN workload_type = 'BI' THEN (db_cores_50th / b.worker_cpu_count)
        ELSE (db_cores_50th / ({db_etl_cores_per_executor} * {db_etl_executors_per_cluster}))
    END AS db_num_clusters_50th,
    CASE
        WHEN workload_type = 'BI' THEN (db_cores_90th / b.worker_cpu_count)
        ELSE (db_cores_90th / ({db_etl_cores_per_executor} * {db_etl_executors_per_cluster}))
    END AS db_num_clusters_90th,
    CASE
        WHEN workload_type = 'BI' THEN (db_cores_99th / b.worker_cpu_count)
        ELSE (db_cores_99th / ({db_etl_cores_per_executor} * {db_etl_executors_per_cluster}))
    END AS db_num_clusters_99th,
    CASE
        WHEN workload_type = 'BI' THEN (db_cores_max / b.worker_cpu_count)
        ELSE (db_cores_max / ({db_etl_cores_per_executor} * {db_etl_executors_per_cluster}))
    END AS db_num_clusters_max,
    CASE
        WHEN workload_type = 'BI' THEN (cumulative_secs_spent_in_exec / {db_sql_performance_factor})
        ELSE (cumulative_secs_spent_in_exec / {db_etl_performance_factor})
    END AS db_secs_spent_in_exec,
    CASE
        WHEN workload_type = 'BI' THEN 0.0
        ELSE (db_num_clusters_avg * ({db_etl_drivers_per_cluster} + {db_etl_executors_per_cluster}) * db_secs_spent_in_exec / 3600.0)
    END AS vm_hours_avg,
    CASE
        WHEN workload_type = 'BI' THEN 0.0
        ELSE (db_num_clusters_50th * ({db_etl_drivers_per_cluster} + {db_etl_executors_per_cluster}) * db_secs_spent_in_exec / 3600.0)
    END AS vm_hours_50th,
    CASE
        WHEN workload_type = 'BI' THEN 0.0
        ELSE (db_num_clusters_90th * ({db_etl_drivers_per_cluster} + {db_etl_executors_per_cluster}) * db_secs_spent_in_exec / 3600.0)
    END AS vm_hours_90th,
    CASE
        WHEN workload_type = 'BI' THEN 0.0
        ELSE (db_num_clusters_99th * ({db_etl_drivers_per_cluster} + {db_etl_executors_per_cluster}) * db_secs_spent_in_exec / 3600.0)
    END AS vm_hours_99th,
    CASE
        WHEN workload_type = 'BI' THEN 0.0
        ELSE (db_num_clusters_max * ({db_etl_drivers_per_cluster} + {db_etl_executors_per_cluster}) * db_secs_spent_in_exec / 3600.0)
    END AS vm_hours_max,
    CASE WHEN workload_type = 'BI' THEN b.dbu_per_hr ELSE c.jobs_photon_dbu END AS dbu_per_hr_multiplier,
    CASE
        WHEN workload_type = 'BI' THEN db_num_clusters_avg * b.dbu_per_hr * db_secs_spent_in_exec / 3600
        ELSE db_num_clusters_avg * ({db_etl_executors_per_cluster} + {db_etl_drivers_per_cluster}) * c.jobs_photon_dbu * db_secs_spent_in_exec / 3600
    END AS num_dbus_avg,
    CASE
        WHEN workload_type = 'BI' THEN db_num_clusters_50th * b.dbu_per_hr * db_secs_spent_in_exec / 3600
        ELSE db_num_clusters_50th * ({db_etl_executors_per_cluster} + {db_etl_drivers_per_cluster}) * c.jobs_photon_dbu * db_secs_spent_in_exec / 3600
    END AS num_dbus_50th,
    CASE
        WHEN workload_type = 'BI' THEN db_num_clusters_90th * b.dbu_per_hr * db_secs_spent_in_exec / 3600
        ELSE db_num_clusters_90th * ({db_etl_executors_per_cluster} + {db_etl_drivers_per_cluster}) * c.jobs_photon_dbu * db_secs_spent_in_exec / 3600
    END AS num_dbus_90th,
    CASE
        WHEN workload_type = 'BI' THEN db_num_clusters_99th * b.dbu_per_hr * db_secs_spent_in_exec / 3600
        ELSE db_num_clusters_99th * ({db_etl_executors_per_cluster} + {db_etl_drivers_per_cluster}) * c.jobs_photon_dbu * db_secs_spent_in_exec / 3600
    END AS num_dbus_99th,
    CASE
        WHEN workload_type = 'BI' THEN db_num_clusters_max * b.dbu_per_hr * db_secs_spent_in_exec / 3600
        ELSE db_num_clusters_max * ({db_etl_executors_per_cluster} + {db_etl_drivers_per_cluster}) * c.jobs_photon_dbu * db_secs_spent_in_exec / 3600
    END AS num_dbus_max,
    CASE
        WHEN workload_type = 'BI' THEN num_dbus_avg * {db_dbsql_pricing}
        ELSE num_dbus_avg * {db_jobs_photon_pricing}
    END AS db_price_avg,
    CASE
        WHEN workload_type = 'BI' THEN num_dbus_50th * {db_dbsql_pricing}
        ELSE num_dbus_50th * {db_jobs_photon_pricing}
    END AS db_price_50th,
    CASE
        WHEN workload_type = 'BI' THEN num_dbus_90th * {db_dbsql_pricing}
        ELSE num_dbus_90th * {db_jobs_photon_pricing}
    END AS db_price_90th,
    CASE
        WHEN workload_type = 'BI' THEN num_dbus_99th * {db_dbsql_pricing}
        ELSE num_dbus_99th * {db_jobs_photon_pricing}
    END AS db_price_99th,
    CASE
        WHEN workload_type = 'BI' THEN num_dbus_max * {db_dbsql_pricing}
        ELSE num_dbus_max * {db_jobs_photon_pricing}
    END AS db_price_max,
    CASE WHEN workload_type = 'BI' THEN 0 ELSE vm_hours_avg * c.vm_per_hr END AS vm_price_avg,
    CASE WHEN workload_type = 'BI' THEN 0 ELSE vm_hours_50th * c.vm_per_hr END AS vm_price_50th,
    CASE WHEN workload_type = 'BI' THEN 0 ELSE vm_hours_90th * c.vm_per_hr END AS vm_price_90th,
    CASE WHEN workload_type = 'BI' THEN 0 ELSE vm_hours_99th * c.vm_per_hr END AS vm_price_99th,
    CASE WHEN workload_type = 'BI' THEN 0 ELSE vm_hours_max * c.vm_per_hr END AS vm_price_max,
    (slot_secs / 3600.0) * {bq_slot_pricing} AS bq_estimated_price,
    CASE
        WHEN workload_type = 'BI' THEN bq_estimated_price / {db_sql_effective_price_perf}
        ELSE bq_estimated_price / {db_etl_effective_price_perf}
    END AS db_price_perf_based,
    slot_secs / 3600 AS slot_hr,
    CASE
        WHEN workload_type = 'BI' THEN slot_hr / ({db_sql_performance_factor} * {db_cores_to_bq_slots_ratio})
        ELSE slot_hr / ({db_etl_performance_factor} * {db_cores_to_bq_slots_ratio})
    END AS db_core_hr,
    CASE
        WHEN workload_type = 'BI' THEN (db_core_hr / b.worker_cpu_count) * b.dbu_per_hr
        ELSE (db_core_hr / {db_etl_cores_per_executor}) * c.jobs_photon_dbu
    END AS dbu_db_core_hr,
    CASE
        WHEN workload_type = 'BI' THEN dbu_db_core_hr * {db_dbsql_pricing}
        ELSE dbu_db_core_hr * {db_jobs_photon_pricing}
    END AS dbu_db_core_price,
    CASE
        WHEN workload_type = 'BI' THEN 0
        ELSE (db_core_hr / {db_etl_cores_per_executor}) * c.vm_per_hr
    END AS dbu_vm_core_price
FROM timeline_analysis a
LEFT JOIN bq_sqlwarehouse_pricing b
       ON b.sku = '{db_sku}'
      AND b.cluster_size = '{db_sql_cluster_size}'
      AND b.cloud = upper('{target_cloud}')
LEFT JOIN bq_cluster_pricing c
       ON c.instance_name = '{etl_instance_type}'
      AND c.cloud = lower('{target_cloud}');
