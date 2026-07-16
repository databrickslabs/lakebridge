"""Utilization and health profiler: merges, mutations, metrics, resource pressure."""

from typing import Any
from databricks.labs.lakebridge.resources.assessments.clickhouse.collectors.base import BaseCollector


class UtilizationCollector(BaseCollector):
    name = "utilization"
    description = "Runtime health, merges, mutations, resource utilization, and performance metrics"

    def collect(self) -> dict[str, Any]:
        d = self.days_back
        results = {}

        # 1. Current server metrics
        results["current_metrics"] = self.safe_query(
            "current_metrics",
            """
            SELECT metric, value, description
            FROM system.metrics
            WHERE value != 0
            ORDER BY value DESC
            """,
        )

        # 2. Cumulative event counters
        results["event_counters"] = self.safe_query(
            "event_counters",
            """
            SELECT event, value, description
            FROM system.events
            WHERE value > 0
            ORDER BY value DESC
            LIMIT 100
            """,
        )

        # 3. Asynchronous metrics (system-level)
        results["async_metrics"] = self.safe_query(
            "async_metrics",
            """
            SELECT metric, value, description
            FROM system.asynchronous_metrics
            WHERE metric IN (
                'MaxPartCountForPartition',
                'NumberOfDatabases',
                'NumberOfTables',
                'TotalRowsOfMergeTreeTables',
                'TotalBytesOfMergeTreeTables',
                'ReplicasMaxAbsoluteDelay',
                'Uptime',
                'jemalloc.resident',
                'jemalloc.allocated',
                'OSMemoryTotal',
                'OSMemoryAvailable',
                'FilesystemMainPathAvailableBytes',
                'FilesystemMainPathTotalBytes',
                'FilesystemMainPathUsedBytes',
                'OSCPUVirtualTimeMicroseconds',
                'CPUFrequencyMHz'
            )
            ORDER BY metric
            """,
        )

        # 4. Active mutations
        results["mutations"] = self.safe_query(
            "mutations",
            """
            SELECT
                database,
                table,
                mutation_id,
                command,
                create_time,
                is_done,
                parts_to_do_names,
                parts_to_do,
                latest_fail_reason
            FROM system.mutations
            WHERE database NOT IN ('system', 'information_schema', 'INFORMATION_SCHEMA')
            ORDER BY create_time DESC
            LIMIT 100
            """,
        )

        # 5. Active merges
        results["active_merges"] = self.safe_query(
            "active_merges",
            """
            SELECT
                database,
                table,
                elapsed,
                progress,
                num_parts,
                result_part_name,
                total_size_bytes_compressed,
                bytes_read_uncompressed,
                rows_read,
                bytes_written_uncompressed,
                rows_written,
                memory_usage,
                is_mutation
            FROM system.merges
            """,
        )

        # 6. Part log summary (merge/insert activity)
        results["part_log_summary"] = self.safe_query(
            "part_log_summary",
            f"""
            SELECT
                event_type,
                database,
                table,
                count() AS event_count,
                sum(rows) AS total_rows,
                sum(size_in_bytes) AS total_bytes,
                avg(duration_ms) AS avg_duration_ms,
                max(duration_ms) AS max_duration_ms,
                sum(peak_memory_usage) AS total_peak_memory
            FROM system.part_log
            WHERE event_time >= now() - INTERVAL {d} DAY
              AND database NOT IN ('system', 'information_schema', 'INFORMATION_SCHEMA')
            GROUP BY event_type, database, table
            ORDER BY event_count DESC
            """,
        )

        # 7. Metric log trends (sampled)
        results["metric_log_trend"] = self.safe_query(
            "metric_log_trend",
            """
            SELECT
                toStartOfHour(event_time) AS hour,
                avg(CurrentMetric_Query) AS avg_running_queries,
                max(CurrentMetric_Query) AS max_running_queries,
                avg(CurrentMetric_Merge) AS avg_running_merges,
                avg(CurrentMetric_MemoryTracking) AS avg_memory_tracking,
                max(CurrentMetric_MemoryTracking) AS max_memory_tracking
            FROM system.metric_log
            GROUP BY hour
            ORDER BY hour
            """,
        )

        # 8. Replicas status (if any)
        results["replicas"] = self.safe_query(
            "replicas",
            """
            SELECT
                database,
                table,
                is_leader,
                is_readonly,
                is_session_expired,
                absolute_delay,
                queue_size,
                inserts_in_queue,
                merges_in_queue,
                total_replicas,
                active_replicas,
                log_pointer,
                last_queue_update
            FROM system.replicas
            """,
        )

        # 9. Storage disks
        results["disks"] = self.safe_query(
            "disks",
            """
            SELECT
                name,
                path,
                free_space,
                total_space,
                keep_free_space,
                type
            FROM system.disks
            """,
        )

        # 10. Workload utilization estimate
        results["workload_utilization"] = self.safe_query(
            "workload_utilization",
            f"""
            SELECT
                toStartOfHour(event_time) AS hour,
                count() AS queries_in_hour,
                sum(query_duration_ms) AS total_query_ms,
                round(sum(query_duration_ms) / 3600000.0, 4) AS query_time_utilization,
                max(peak_threads_usage) AS max_threads_used,
                avg(peak_threads_usage) AS avg_threads_used,
                avg(memory_usage) AS avg_memory_usage,
                max(memory_usage) AS max_memory_usage
            FROM system.query_log
            WHERE type = 'QueryFinish'
              AND is_initial_query = 1
              AND event_time >= now() - INTERVAL {d} DAY
            GROUP BY hour
            ORDER BY hour
            """,
        )

        # 11. Cluster topology (shards, replicas, nodes)
        results["cluster_topology"] = self.safe_query(
            "cluster_topology",
            """
            SELECT
                cluster,
                count(DISTINCT shard_num) AS shards,
                count(DISTINCT replica_num) AS replicas_per_shard,
                count(*) AS total_nodes,
                groupArray(DISTINCT host_name) AS host_names
            FROM system.clusters
            WHERE cluster NOT IN ('system_metrics_log_cluster')
            GROUP BY cluster
            """,
        )

        # 12. Server hardware profile (CPU cores, total memory, OS)
        results["server_resources"] = self.safe_query(
            "server_resources",
            """
            SELECT
                (SELECT value FROM system.asynchronous_metrics WHERE metric = 'OSMemoryTotal') AS total_memory_bytes,
                (SELECT value FROM system.asynchronous_metrics WHERE metric = 'OSMemoryAvailable') AS available_memory_bytes,
                (SELECT round(value / (1024*1024*1024), 2) FROM system.asynchronous_metrics WHERE metric = 'OSMemoryTotal') AS total_memory_gb,
                (SELECT value FROM system.asynchronous_metrics WHERE metric = 'Uptime') AS uptime_seconds,
                (SELECT value FROM system.asynchronous_metrics WHERE metric = 'FilesystemMainPathTotalBytes') AS fs_total_bytes,
                (SELECT value FROM system.asynchronous_metrics WHERE metric = 'FilesystemMainPathAvailableBytes') AS fs_available_bytes
            """,
        )

        # 13. Peak and average CPU/memory utilization over profiling window
        results["resource_utilization_trend"] = self.safe_query(
            "resource_utilization_trend",
            f"""
            SELECT
                toStartOfDay(event_time) AS day,
                round(avg(ProfileEvent_OSCPUVirtualTimeMicroseconds) / 1000000, 2) AS avg_cpu_seconds,
                round(max(ProfileEvent_OSCPUVirtualTimeMicroseconds) / 1000000, 2) AS peak_cpu_seconds,
                round(avg(CurrentMetric_MemoryTracking) / (1024*1024*1024), 2) AS avg_memory_gb,
                round(max(CurrentMetric_MemoryTracking) / (1024*1024*1024), 2) AS peak_memory_gb,
                avg(CurrentMetric_Query) AS avg_concurrent_queries,
                max(CurrentMetric_Query) AS peak_concurrent_queries,
                avg(CurrentMetric_Merge) AS avg_concurrent_merges,
                max(CurrentMetric_Merge) AS peak_concurrent_merges
            FROM system.metric_log
            WHERE event_time >= now() - INTERVAL {d} DAY
            GROUP BY day
            ORDER BY day
            """,
        )

        # 14. Monthly compute footprint summary
        results["compute_footprint"] = self.safe_query(
            "compute_footprint",
            f"""
            SELECT
                count() AS total_queries,
                round(sum(query_duration_ms) / 1000.0, 2) AS total_compute_seconds,
                round(sum(query_duration_ms) / 3600000.0, 2) AS total_compute_hours,
                round(sum(query_duration_ms * greatest(peak_threads_usage, 1)) / 3600000.0, 2) AS total_thread_hours,
                max(peak_threads_usage) AS max_peak_threads,
                round(avg(peak_threads_usage), 2) AS avg_peak_threads,
                round(avg(memory_usage) / (1024*1024*1024), 2) AS avg_query_memory_gb,
                round(max(memory_usage) / (1024*1024*1024), 2) AS max_query_memory_gb,
                round(sum(read_bytes) / (1024*1024*1024*1024), 2) AS total_data_scanned_tb,
                round(sum(written_bytes) / (1024*1024*1024), 2) AS total_data_written_gb,
                round(countIf(query_duration_ms > 30000) * 100.0 / count(), 2) AS pct_queries_over_30s,
                toStartOfMonth(min(event_time)) AS period_start,
                toStartOfMonth(max(event_time)) AS period_end
            FROM system.query_log
            WHERE type = 'QueryFinish'
              AND is_initial_query = 1
              AND event_time >= now() - INTERVAL {d} DAY
            """,
        )

        return results
