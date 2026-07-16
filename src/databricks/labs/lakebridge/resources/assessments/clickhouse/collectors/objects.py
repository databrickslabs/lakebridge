"""Objects and artifacts inventory: databases, tables, columns, parts, storage layout."""

from typing import Any
from databricks.labs.lakebridge.resources.assessments.clickhouse.collectors.base import BaseCollector


class ObjectsCollector(BaseCollector):
    name = "objects"
    description = "Database objects, tables, columns, storage layout, and physical footprint"

    def collect(self) -> dict[str, Any]:
        results = {}

        # 1. Database inventory
        results["databases"] = self.safe_query(
            "databases",
            """
            SELECT
                name,
                engine,
                data_path,
                metadata_path,
                uuid
            FROM system.databases
            WHERE name NOT IN ('system', 'information_schema', 'INFORMATION_SCHEMA')
            ORDER BY name
            """,
        )

        # 2. Table inventory with full metadata
        results["tables"] = self.safe_query(
            "tables",
            """
            SELECT
                database,
                name,
                engine,
                partition_key,
                sorting_key,
                primary_key,
                sampling_key,
                total_rows,
                total_bytes,
                lifetime_rows,
                lifetime_bytes,
                has_own_data,
                dependencies_database,
                dependencies_table,
                create_table_query,
                engine_full,
                as_select,
                comment
            FROM system.tables
            WHERE database NOT IN ('system', 'information_schema', 'INFORMATION_SCHEMA')
            ORDER BY database, name
            """,
        )

        # 3. Object count by engine type
        results["engine_distribution"] = self.safe_query(
            "engine_distribution",
            """
            SELECT
                database,
                engine,
                count() AS object_count,
                sum(total_rows) AS sum_rows,
                sum(total_bytes) AS sum_bytes,
                formatReadableSize(sum(total_bytes)) AS total_size_readable
            FROM system.tables
            WHERE database NOT IN ('system', 'information_schema', 'INFORMATION_SCHEMA')
            GROUP BY database, engine
            ORDER BY database, object_count DESC
            """,
        )

        # 4. Column inventory with types and sizes
        results["columns"] = self.safe_query(
            "columns",
            """
            SELECT
                database,
                table,
                name AS column_name,
                type,
                default_kind,
                default_expression,
                data_compressed_bytes,
                data_uncompressed_bytes,
                marks_bytes,
                comment,
                is_in_partition_key,
                is_in_sorting_key,
                is_in_primary_key,
                is_in_sampling_key
            FROM system.columns
            WHERE database NOT IN ('system', 'information_schema', 'INFORMATION_SCHEMA')
            ORDER BY database, table, position
            """,
        )

        # 5. Data type distribution
        results["data_type_distribution"] = self.safe_query(
            "data_type_distribution",
            """
            SELECT
                type,
                count() AS usage_count,
                uniqExact(database, table) AS distinct_tables,
                sum(data_compressed_bytes) AS total_compressed_bytes,
                sum(data_uncompressed_bytes) AS total_uncompressed_bytes
            FROM system.columns
            WHERE database NOT IN ('system', 'information_schema', 'INFORMATION_SCHEMA')
            GROUP BY type
            ORDER BY usage_count DESC
            """,
        )

        # 6. Parts and storage layout
        results["parts_summary"] = self.safe_query(
            "parts_summary",
            """
            SELECT
                database,
                table,
                count() AS active_parts,
                sum(rows) AS total_rows,
                sum(bytes_on_disk) AS total_bytes_on_disk,
                formatReadableSize(sum(bytes_on_disk)) AS size_on_disk,
                sum(data_compressed_bytes) AS data_compressed,
                sum(data_uncompressed_bytes) AS data_uncompressed,
                round(sum(data_uncompressed_bytes) / nullIf(sum(data_compressed_bytes), 0), 2) AS compression_ratio,
                min(min_date) AS min_date,
                max(max_date) AS max_date,
                count(DISTINCT partition_id) AS partition_count
            FROM system.parts
            WHERE active
              AND database NOT IN ('system', 'information_schema', 'INFORMATION_SCHEMA')
            GROUP BY database, table
            ORDER BY total_bytes_on_disk DESC
            """,
        )

        # 7. Largest columns by compressed footprint (via parts_columns for Cloud compat)
        results["largest_columns"] = self.safe_query(
            "largest_columns",
            """
            SELECT
                database,
                table,
                column AS column_name,
                type,
                sum(column_data_compressed_bytes) AS compressed_bytes,
                sum(column_data_uncompressed_bytes) AS uncompressed_bytes,
                formatReadableSize(sum(column_data_compressed_bytes)) AS compressed_readable,
                round(sum(column_data_uncompressed_bytes) / nullIf(sum(column_data_compressed_bytes), 0), 2) AS compression_ratio
            FROM system.parts_columns
            WHERE active
              AND database NOT IN ('system', 'information_schema', 'INFORMATION_SCHEMA')
            GROUP BY database, table, column, type
            HAVING compressed_bytes > 0
            ORDER BY compressed_bytes DESC
            LIMIT 100
            """,
        )

        # 8. Projections
        results["projections"] = self.safe_query(
            "projections",
            """
            SELECT
                database,
                table,
                name AS projection_name,
                type,
                sorting_key
            FROM system.projections
            WHERE database NOT IN ('system', 'information_schema', 'INFORMATION_SCHEMA')
            ORDER BY database, table
            """,
        )

        # 9. Data skipping indices
        results["skipping_indices"] = self.safe_query(
            "skipping_indices",
            """
            SELECT
                database,
                table,
                name AS index_name,
                type AS index_type,
                expr,
                granularity,
                data_compressed_bytes,
                data_uncompressed_bytes
            FROM system.data_skipping_indices
            WHERE database NOT IN ('system', 'information_schema', 'INFORMATION_SCHEMA')
            ORDER BY database, table
            """,
        )

        # 10. Dictionaries
        results["dictionaries"] = self.safe_query(
            "dictionaries",
            """
            SELECT
                database,
                name,
                status,
                origin,
                type,
                key.names AS key_names,
                key.types AS key_types,
                attribute.names AS attr_names,
                attribute.types AS attr_types,
                element_count,
                bytes_allocated,
                loading_duration,
                last_successful_update_time,
                loading_start_time,
                source
            FROM system.dictionaries
            """,
        )

        return results
