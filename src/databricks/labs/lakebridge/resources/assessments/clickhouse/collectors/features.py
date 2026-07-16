"""Feature usage profiler: what ClickHouse features are actively being used."""

from typing import Any
from databricks.labs.lakebridge.resources.assessments.clickhouse.collectors.base import BaseCollector


class FeaturesCollector(BaseCollector):
    name = "features"
    description = "Active feature usage: engines, functions, formats, table functions, storages"

    def collect(self) -> dict[str, Any]:
        d = self.days_back
        results = {}

        # 1. Table engine usage (from metadata)
        results["engine_usage"] = self.safe_query(
            "engine_usage",
            """
            SELECT
                engine,
                count() AS table_count,
                sum(total_rows) AS total_rows,
                sum(total_bytes) AS total_bytes
            FROM system.tables
            WHERE database NOT IN ('system', 'information_schema', 'INFORMATION_SCHEMA')
            GROUP BY engine
            ORDER BY table_count DESC
            """,
        )

        # 2. Functions used in queries (from query_log used_functions array)
        results["functions_used"] = self.safe_query(
            "functions_used",
            f"""
            SELECT
                func,
                count() AS query_count,
                uniqExact(user) AS distinct_users
            FROM system.query_log
            ARRAY JOIN used_functions AS func
            WHERE type = 'QueryFinish'
              AND is_initial_query = 1
              AND event_time >= now() - INTERVAL {d} DAY
            GROUP BY func
            ORDER BY query_count DESC
            LIMIT 100
            """,
        )

        # 3. Storage types used in queries
        results["storages_used"] = self.safe_query(
            "storages_used",
            f"""
            SELECT
                storage,
                count() AS query_count,
                uniqExact(user) AS distinct_users
            FROM system.query_log
            ARRAY JOIN used_storages AS storage
            WHERE type = 'QueryFinish'
              AND event_time >= now() - INTERVAL {d} DAY
            GROUP BY storage
            ORDER BY query_count DESC
            """,
        )

        # 4. Table functions used
        results["table_functions_used"] = self.safe_query(
            "table_functions_used",
            f"""
            SELECT
                tf,
                count() AS query_count,
                uniqExact(user) AS distinct_users
            FROM system.query_log
            ARRAY JOIN used_table_functions AS tf
            WHERE type = 'QueryFinish'
              AND event_time >= now() - INTERVAL {d} DAY
            GROUP BY tf
            ORDER BY query_count DESC
            """,
        )

        # 5. Data formats used
        results["formats_used"] = self.safe_query(
            "formats_used",
            f"""
            SELECT
                fmt,
                count() AS query_count
            FROM system.query_log
            ARRAY JOIN used_formats AS fmt
            WHERE type = 'QueryFinish'
              AND event_time >= now() - INTERVAL {d} DAY
            GROUP BY fmt
            ORDER BY query_count DESC
            """,
        )

        # 6. Aggregate functions used
        results["aggregate_functions_used"] = self.safe_query(
            "aggregate_functions_used",
            f"""
            SELECT
                af,
                count() AS query_count
            FROM system.query_log
            ARRAY JOIN used_aggregate_functions AS af
            WHERE type = 'QueryFinish'
              AND event_time >= now() - INTERVAL {d} DAY
            GROUP BY af
            ORDER BY query_count DESC
            LIMIT 50
            """,
        )

        # 7. Materialized views
        results["materialized_views"] = self.safe_query(
            "materialized_views",
            """
            SELECT
                database,
                name,
                engine,
                as_select,
                dependencies_database,
                dependencies_table,
                create_table_query
            FROM system.tables
            WHERE engine = 'MaterializedView'
              AND database NOT IN ('system', 'information_schema', 'INFORMATION_SCHEMA')
            ORDER BY database, name
            """,
        )

        # 8. Regular views
        results["views"] = self.safe_query(
            "views",
            """
            SELECT
                database,
                name,
                as_select,
                create_table_query
            FROM system.tables
            WHERE engine = 'View'
              AND database NOT IN ('system', 'information_schema', 'INFORMATION_SCHEMA')
            ORDER BY database, name
            """,
        )

        # 9. View refresh status (for refreshable MVs)
        results["view_refreshes"] = self.safe_query(
            "view_refreshes",
            """
            SELECT *
            FROM system.view_refreshes
            """,
        )

        # 10. TTL usage
        results["ttl_tables"] = self.safe_query(
            "ttl_tables",
            """
            SELECT
                database,
                name,
                engine,
                create_table_query
            FROM system.tables
            WHERE database NOT IN ('system', 'information_schema', 'INFORMATION_SCHEMA')
              AND create_table_query LIKE '%TTL%'
            ORDER BY database, name
            """,
        )

        # 11. Partitioned tables
        results["partitioned_tables"] = self.safe_query(
            "partitioned_tables",
            """
            SELECT
                database,
                name,
                engine,
                partition_key,
                sorting_key,
                total_rows,
                total_bytes
            FROM system.tables
            WHERE database NOT IN ('system', 'information_schema', 'INFORMATION_SCHEMA')
              AND partition_key != ''
            ORDER BY total_bytes DESC
            """,
        )

        # 12. Available table engines and functions (capability inventory)
        results["available_table_engines"] = self.safe_query(
            "available_table_engines",
            "SELECT name, supports_settings, supports_skipping_indices, supports_projections, supports_sort_order, supports_replication FROM system.table_engines ORDER BY name",
        )

        return results
