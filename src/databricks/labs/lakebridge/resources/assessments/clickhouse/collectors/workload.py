"""Workload usage profiler: query history, patterns, performance, concurrency."""

from typing import Any
from databricks.labs.lakebridge.resources.assessments.clickhouse.collectors.base import BaseCollector


class WorkloadCollector(BaseCollector):
    name = "workload"
    description = "Query activity, performance, concurrency, and workload patterns"

    def collect(self) -> dict[str, Any]:
        d = self.days_back
        results = {}

        # 1. Query volume summary
        results["query_volume_summary"] = self.safe_query(
            "query_volume_summary",
            f"""
            SELECT
                query_kind,
                count() AS total_queries,
                countIf(type = 'QueryFinish') AS succeeded,
                countIf(type IN ('ExceptionBeforeStart', 'ExceptionWhileProcessing')) AS failed,
                round(countIf(type IN ('ExceptionBeforeStart', 'ExceptionWhileProcessing')) /
                      nullIf(count(), 0) * 100, 2) AS error_rate_pct,
                uniqExact(user) AS distinct_users,
                uniqExact(normalized_query_hash) AS distinct_query_shapes,
                sum(read_bytes) AS total_read_bytes,
                sum(written_bytes) AS total_written_bytes,
                sum(read_rows) AS total_read_rows,
                sum(written_rows) AS total_written_rows,
                sum(result_rows) AS total_result_rows
            FROM {self.source('query_log')}
            WHERE event_time >= now() - INTERVAL {d} DAY
              AND is_initial_query = 1
            GROUP BY query_kind
            ORDER BY total_queries DESC
            """,
        )

        # 2. Daily query trend
        results["daily_query_trend"] = self.safe_query(
            "daily_query_trend",
            f"""
            SELECT
                toDate(event_time) AS day,
                count() AS total_queries,
                countIf(type = 'QueryFinish') AS succeeded,
                countIf(type IN ('ExceptionBeforeStart', 'ExceptionWhileProcessing')) AS failed,
                uniqExact(user) AS active_users,
                sum(read_bytes) AS read_bytes,
                sum(written_bytes) AS written_bytes
            FROM {self.source('query_log')}
            WHERE event_time >= now() - INTERVAL {d} DAY
              AND is_initial_query = 1
            GROUP BY day
            ORDER BY day
            """,
        )

        # 3. Top queries by frequency (normalized)
        results["top_queries_by_frequency"] = self.safe_query(
            "top_queries_by_frequency",
            f"""
            SELECT
                normalized_query_hash,
                any(query_kind) AS query_kind,
                any(user) AS sample_user,
                count() AS executions,
                quantileTDigest(0.5)(query_duration_ms) AS p50_ms,
                quantileTDigest(0.95)(query_duration_ms) AS p95_ms,
                quantileTDigest(0.99)(query_duration_ms) AS p99_ms,
                avg(memory_usage) AS avg_memory,
                max(memory_usage) AS max_memory,
                sum(read_bytes) AS total_read_bytes,
                sum(read_rows) AS total_read_rows,
                any(query) AS sample_query
            FROM {self.source('query_log')}
            WHERE type = 'QueryFinish'
              AND is_initial_query = 1
              AND event_time >= now() - INTERVAL {d} DAY
            GROUP BY normalized_query_hash
            ORDER BY executions DESC
            LIMIT 100
            """,
        )

        # 4. Slowest queries
        results["slowest_queries"] = self.safe_query(
            "slowest_queries",
            f"""
            SELECT
                event_time,
                user,
                query_kind,
                query_duration_ms,
                read_rows,
                read_bytes,
                written_rows,
                written_bytes,
                memory_usage,
                result_rows,
                databases,
                tables,
                query
            FROM {self.source('query_log')}
            WHERE type = 'QueryFinish'
              AND is_initial_query = 1
              AND event_time >= now() - INTERVAL {d} DAY
            ORDER BY query_duration_ms DESC
            LIMIT 50
            """,
        )

        # 5. Most expensive queries (by bytes scanned)
        results["most_expensive_queries"] = self.safe_query(
            "most_expensive_queries",
            f"""
            SELECT
                normalized_query_hash,
                count() AS runs,
                sum(read_bytes) AS total_read_bytes,
                avg(read_bytes) AS avg_read_bytes,
                sum(read_rows) AS total_read_rows,
                quantileTDigest(0.95)(query_duration_ms) AS p95_ms,
                avg(memory_usage) AS avg_memory,
                any(query) AS sample_query
            FROM {self.source('query_log')}
            WHERE type = 'QueryFinish'
              AND is_initial_query = 1
              AND event_time >= now() - INTERVAL {d} DAY
            GROUP BY normalized_query_hash
            ORDER BY total_read_bytes DESC
            LIMIT 50
            """,
        )

        # 6. Hourly concurrency pattern
        results["hourly_query_pattern"] = self.safe_query(
            "hourly_query_pattern",
            f"""
            SELECT
                toHour(event_time) AS hour_of_day,
                count() AS query_count,
                uniqExact(user) AS distinct_users,
                avg(query_duration_ms) AS avg_duration_ms,
                quantileTDigest(0.95)(query_duration_ms) AS p95_duration_ms,
                quantileTDigest(0.99)(query_duration_ms) AS p99_duration_ms,
                sum(read_bytes) AS total_read_bytes
            FROM {self.source('query_log')}
            WHERE type = 'QueryFinish'
              AND is_initial_query = 1
              AND event_time >= now() - INTERVAL {d} DAY
            GROUP BY hour_of_day
            ORDER BY hour_of_day
            """,
        )

        # 7. Query type classification (like Redshift approach)
        results["query_type_classification"] = self.safe_query(
            "query_type_classification",
            f"""
            SELECT
                multiIf(
                    query_kind = 'Select', 'BI Query / Analytics',
                    query_kind = 'Insert', 'Data Ingestion',
                    query_kind IN ('Create', 'Alter', 'Drop', 'Rename'), 'DDL',
                    query_kind IN ('Grant', 'Revoke'), 'Security',
                    query_kind = 'Optimize', 'Maintenance',
                    query_kind = 'System', 'System',
                    query_kind IN ('Delete', 'Update'), 'DML / Transform',
                    query_kind = 'Explain', 'Explain',
                    'Other'
                ) AS query_category,
                count() AS query_count,
                round(count() * 100.0 / sum(count()) OVER (), 2) AS pct_of_total,
                uniqExact(user) AS distinct_users,
                sum(read_bytes) AS total_read_bytes,
                avg(query_duration_ms) AS avg_duration_ms
            FROM {self.source('query_log')}
            WHERE type = 'QueryFinish'
              AND is_initial_query = 1
              AND event_time >= now() - INTERVAL {d} DAY
            GROUP BY query_category
            ORDER BY query_count DESC
            """,
        )

        # 8. Per-user workload summary
        results["user_workload_summary"] = self.safe_query(
            "user_workload_summary",
            f"""
            SELECT
                user,
                count() AS total_queries,
                uniqExact(query_kind) AS query_kinds_used,
                uniqExact(normalized_query_hash) AS distinct_queries,
                sum(read_bytes) AS total_read_bytes,
                sum(written_bytes) AS total_written_bytes,
                avg(query_duration_ms) AS avg_duration_ms,
                quantileTDigest(0.95)(query_duration_ms) AS p95_duration_ms,
                quantileTDigest(0.99)(query_duration_ms) AS p99_duration_ms,
                max(memory_usage) AS peak_memory,
                min(event_time) AS first_seen,
                max(event_time) AS last_seen
            FROM {self.source('query_log')}
            WHERE type = 'QueryFinish'
              AND is_initial_query = 1
              AND event_time >= now() - INTERVAL {d} DAY
            GROUP BY user
            ORDER BY total_queries DESC
            """,
        )

        # 9. Latency SLA report (p50/p95/p99 by query kind)
        results["latency_sla"] = self.safe_query(
            "latency_sla",
            f"""
            SELECT
                query_kind,
                count() AS runs,
                quantileTDigest(0.5)(query_duration_ms) AS p50_ms,
                quantileTDigest(0.75)(query_duration_ms) AS p75_ms,
                quantileTDigest(0.95)(query_duration_ms) AS p95_ms,
                quantileTDigest(0.99)(query_duration_ms) AS p99_ms,
                max(query_duration_ms) AS max_ms
            FROM {self.source('query_log')}
            WHERE type = 'QueryFinish'
              AND is_initial_query = 1
              AND event_time >= now() - INTERVAL {d} DAY
            GROUP BY query_kind
            ORDER BY runs DESC
            """,
        )

        # 10. Error analysis
        results["error_analysis"] = self.safe_query(
            "error_analysis",
            f"""
            SELECT
                exception_code,
                any(exception) AS sample_exception,
                count() AS occurrences,
                uniqExact(user) AS affected_users,
                uniqExact(normalized_query_hash) AS distinct_queries,
                min(event_time) AS first_seen,
                max(event_time) AS last_seen
            FROM {self.source('query_log')}
            WHERE type IN ('ExceptionBeforeStart', 'ExceptionWhileProcessing')
              AND event_time >= now() - INTERVAL {d} DAY
            GROUP BY exception_code
            ORDER BY occurrences DESC
            LIMIT 50
            """,
        )

        return results
