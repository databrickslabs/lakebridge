"""Dependencies and lineage profiler: object dependencies, runtime access, view execution."""

from typing import Any
from databricks.labs.lakebridge.resources.assessments.clickhouse.collectors.base import BaseCollector


class DependenciesCollector(BaseCollector):
    name = "dependencies"
    description = "Object dependencies, runtime lineage, and view execution chains"

    def collect(self) -> dict[str, Any]:
        d = self.days_back
        results = {}

        # 1. Object dependency graph (from system.tables)
        results["object_dependencies"] = self.safe_query(
            "object_dependencies",
            """
            SELECT
                database,
                name,
                engine,
                dependencies_database,
                dependencies_table
            FROM system.tables
            WHERE database NOT IN ('system', 'information_schema', 'INFORMATION_SCHEMA')
              AND length(dependencies_table) > 0
            ORDER BY database, name
            """,
        )

        # 2. Runtime table access map (which tables are accessed by queries)
        results["runtime_table_access"] = self.safe_query(
            "runtime_table_access",
            f"""
            SELECT
                user,
                normalized_query_hash,
                any(query_kind) AS query_kind,
                groupUniqArrayArray(databases) AS accessed_databases,
                groupUniqArrayArray(tables) AS accessed_tables,
                count() AS runs,
                sum(read_bytes) AS total_read_bytes
            FROM system.query_log
            WHERE type = 'QueryFinish'
              AND is_initial_query = 1
              AND event_time >= now() - INTERVAL {d} DAY
              AND length(tables) > 0
            GROUP BY user, normalized_query_hash
            ORDER BY runs DESC
            LIMIT 200
            """,
        )

        # 3. Table usage frequency (how often each table is accessed)
        results["table_usage_frequency"] = self.safe_query(
            "table_usage_frequency",
            f"""
            SELECT
                tbl,
                count() AS access_count,
                uniqExact(user) AS distinct_users,
                uniqExact(query_kind) AS query_kinds,
                sum(read_bytes) AS total_read_bytes,
                groupUniqArray(query_kind) AS query_kind_list
            FROM system.query_log
            ARRAY JOIN tables AS tbl
            WHERE type = 'QueryFinish'
              AND is_initial_query = 1
              AND event_time >= now() - INTERVAL {d} DAY
            GROUP BY tbl
            ORDER BY access_count DESC
            LIMIT 100
            """,
        )

        # 4. Column usage frequency
        results["column_usage_frequency"] = self.safe_query(
            "column_usage_frequency",
            f"""
            SELECT
                col,
                count() AS access_count,
                uniqExact(user) AS distinct_users
            FROM system.query_log
            ARRAY JOIN columns AS col
            WHERE type = 'QueryFinish'
              AND is_initial_query = 1
              AND event_time >= now() - INTERVAL {d} DAY
            GROUP BY col
            ORDER BY access_count DESC
            LIMIT 200
            """,
        )

        # 5. View execution lineage (from query_views_log)
        results["view_execution_lineage"] = self.safe_query(
            "view_execution_lineage",
            f"""
            SELECT
                view_name,
                view_type,
                view_target,
                view_query,
                count() AS executions,
                sum(read_rows) AS total_read_rows,
                sum(read_bytes) AS total_read_bytes,
                sum(written_rows) AS total_written_rows,
                sum(written_bytes) AS total_written_bytes,
                avg(view_duration_ms) AS avg_duration_ms,
                max(peak_memory_usage) AS max_memory
            FROM system.query_views_log
            WHERE event_time >= now() - INTERVAL {d} DAY
            GROUP BY view_name, view_type, view_target, view_query
            ORDER BY executions DESC
            """,
        )

        # 6. Unused tables (tables with no query access in the period)
        results["potentially_unused_tables"] = self.safe_query(
            "potentially_unused_tables",
            f"""
            WITH accessed AS (
                SELECT DISTINCT arrayJoin(tables) AS tbl
                FROM system.query_log
                WHERE type = 'QueryFinish'
                  AND event_time >= now() - INTERVAL {d} DAY
            )
            SELECT
                t.database,
                t.name,
                t.engine,
                t.total_rows,
                t.total_bytes,
                formatReadableSize(t.total_bytes) AS size_readable
            FROM system.tables t
            LEFT JOIN accessed a ON concat(t.database, '.', t.name) = a.tbl
            WHERE t.database NOT IN ('system', 'information_schema', 'INFORMATION_SCHEMA')
              AND a.tbl IS NULL
              AND t.total_rows > 0
            ORDER BY t.total_bytes DESC
            """,
        )

        return results
