from collections.abc import Iterable

from databricks.labs.lakebridge.connections.database_manager import DatabaseManager
from databricks.labs.lakebridge.discovery.table import TableDefinition, TableFQN, FieldInfo
from databricks.labs.lakebridge.discovery.table_definition import TableDefinitionService


class TsqlTableDefinitionService(TableDefinitionService):

    # Hexadecimal value of § is U+00A7.Hexadecimal value of ‡ (double dagger) is U+2021
    def _get_table_definition_query(self, catalog_name: str) -> str:
        query = f"""
        WITH column_info AS (
            SELECT
                TABLE_CATALOG,
                TABLE_SCHEMA,
                TABLE_NAME,
                STRING_AGG(
                    CONCAT(
                        column_name,
                        '§',
                        CASE
                            WHEN numeric_precision IS NOT NULL AND numeric_scale IS NOT NULL THEN CONCAT(data_type, '(', numeric_precision, ',', numeric_scale, ')')
                            WHEN LOWER(data_type) = 'text' THEN CONCAT('varchar', '(', CHARACTER_MAXIMUM_LENGTH, ')')
                            ELSE data_type
                        END,
                        '§',
                        CASE
                            WHEN cis.IS_NULLABLE = 'YES' THEN 'true'
                            ELSE 'false'
                        END,
                        '§',
                        ISNULL(CAST(ep_col.value AS NVARCHAR(MAX)), '')
                    ),
                    '‡'
                ) WITHIN GROUP (ORDER BY ordinal_position) AS DERIVED_SCHEMA
            FROM
                {catalog_name}.sys.tables t
                INNER JOIN {catalog_name}.sys.columns c ON t.object_id = c.object_id
                INNER JOIN {catalog_name}.INFORMATION_SCHEMA.COLUMNS cis ON t.name = cis.TABLE_NAME AND c.name = cis.COLUMN_NAME
                OUTER APPLY (
                    SELECT TOP 1 value
                    FROM {catalog_name}.sys.extended_properties
                    WHERE major_id = t.object_id AND minor_id = 0
                    ORDER BY name DESC
                ) ep_tbl
                OUTER APPLY (
                    SELECT TOP 1 value
                    FROM {catalog_name}.sys.extended_properties
                    WHERE major_id = c.object_id AND minor_id = c.column_id
                    ORDER BY name DESC
                ) ep_col
            GROUP BY
                TABLE_CATALOG,
                TABLE_SCHEMA,
                TABLE_NAME
        ),
        table_file_info AS (
            SELECT
                s.name AS TABLE_SCHEMA,
                t.name AS TABLE_NAME,
                f.physical_name AS location,
                f.type_desc AS TABLE_FORMAT,
                CAST(ROUND(SUM(a.used_pages) * 8.0 / 1024, 2) AS DECIMAL(18, 2)) AS SIZE_GB
            FROM
                {catalog_name}.sys.tables t
                INNER JOIN {catalog_name}.sys.indexes i ON t.object_id = i.object_id
                INNER JOIN {catalog_name}.sys.partitions p ON i.object_id = p.object_id AND i.index_id = p.index_id
                INNER JOIN {catalog_name}.sys.allocation_units a ON p.partition_id = a.container_id
                INNER JOIN {catalog_name}.sys.schemas s ON t.schema_id = s.schema_id
                INNER JOIN {catalog_name}.sys.database_files f ON a.data_space_id = f.data_space_id
                LEFT JOIN {catalog_name}.sys.extended_properties ep ON ep.major_id = t.object_id AND ep.minor_id = 0
            GROUP BY
                s.name,
                t.name,
                f.name,
                f.physical_name,
                f.type_desc
        ),
        table_comment_info AS (
            SELECT
                s.name AS TABLE_SCHEMA,
                t.name AS TABLE_NAME,
                CAST(ep.value AS NVARCHAR(MAX)) AS TABLE_COMMENT
            FROM
                {catalog_name}.sys.tables t
                INNER JOIN {catalog_name}.sys.schemas s ON t.schema_id = s.schema_id
                OUTER APPLY (
                    SELECT TOP 1 value
                    FROM {catalog_name}.sys.extended_properties
                    WHERE major_id = t.object_id AND minor_id = 0
                    ORDER BY name DESC
                ) ep
        ),
        table_pk_info AS (
            SELECT
                TC.TABLE_CATALOG,
                TC.TABLE_SCHEMA,
                TC.TABLE_NAME,
                STRING_AGG(KU.COLUMN_NAME,':') as PK_COLUMN_NAME
                FROM {catalog_name}.INFORMATION_SCHEMA.TABLE_CONSTRAINTS AS TC
                JOIN {catalog_name}.INFORMATION_SCHEMA.KEY_COLUMN_USAGE AS KU
                ON TC.CONSTRAINT_NAME = KU.CONSTRAINT_NAME
                AND TC.TABLE_NAME = KU.TABLE_NAME
                WHERE TC.CONSTRAINT_TYPE = 'PRIMARY KEY' group by TC.TABLE_CATALOG, TC.TABLE_SCHEMA, TC.TABLE_NAME)
        SELECT
            sft.TABLE_CATALOG,
            sft.TABLE_SCHEMA,
            sft.TABLE_NAME,
            tfi.location,
            tfi.TABLE_FORMAT,
            '' as view_definition,
            column_info.DERIVED_SCHEMA,
            tfi.SIZE_GB,
            tci.TABLE_COMMENT,
            tpK.PK_COLUMN_NAME
        FROM
            column_info
            JOIN {catalog_name}.INFORMATION_SCHEMA.TABLES sft ON column_info.TABLE_CATALOG = sft.TABLE_CATALOG AND column_info.TABLE_SCHEMA = sft.TABLE_SCHEMA AND column_info.TABLE_NAME = sft.TABLE_NAME
            LEFT JOIN table_file_info tfi ON column_info.TABLE_SCHEMA = tfi.TABLE_SCHEMA AND column_info.TABLE_NAME = tfi.TABLE_NAME
            LEFT JOIN table_comment_info tci ON column_info.TABLE_SCHEMA = tci.TABLE_SCHEMA AND column_info.TABLE_NAME = tci.TABLE_NAME
            LEFT JOIN table_pk_info tpK ON column_info.TABLE_SCHEMA = tpK.TABLE_SCHEMA AND column_info.TABLE_NAME = tpK.TABLE_NAME

        UNION ALL
        SELECT
            sfv.TABLE_CATALOG,
            sfv.TABLE_SCHEMA,
            sfv.TABLE_NAME,
            '' location,
            '' TABLE_FORMAT,
            sfv.view_definition,
            '' DERIVED_SCHEMA,
            0 SIZE_GB,
            '' TABLE_COMMENT,
            '' PK_COLUMN_NAME
        FROM {catalog_name}.INFORMATION_SCHEMA.VIEWS sfv
        """
        return query

    def get_table_definition(self, catalog_name: str) -> Iterable[TableDefinition]:
        sql = self._get_table_definition_query(catalog_name)
        tsql_connection = self.connection
        result = tsql_connection.execute_query(sql)

        column_names = list(result.keys())
        table_definitions = []

        for row in result:
            result = dict(zip(column_names, row))
            table_fqn = TableFQN(
                catalog=result["TABLE_CATALOG"], schema=result["TABLE_SCHEMA"], name=result["TABLE_NAME"]
            )
            columns = result["DERIVED_SCHEMA"].split("‡") if result["DERIVED_SCHEMA"] else None
            field_info = []
            if columns is not None:
                for column in columns:
                    column_info = column.split("§")
                    field = FieldInfo(
                        name=column_info[0],
                        data_type=column_info[1],
                        nullable=column_info[2],
                        comment=column_info[3],
                    )
                    field_info.append(field)

            pks = result["PK_COLUMN_NAME"].split(":") if result["PK_COLUMN_NAME"] else None
            table_definition = TableDefinition(
                fqn=table_fqn,
                location=result["location"],
                table_format=result["TABLE_FORMAT"],
                view_text=result["view_definition"],
                columns=field_info,
                size_gb=result["SIZE_GB"],
                comment=result["TABLE_COMMENT"],
                primary_keys=pks,
            )
            table_definitions.append(table_definition)
        return table_definitions

    def get_all_catalog(self) -> Iterable[str]:
        cursor: DatabaseManager = self.connection
        result = cursor.connector.execute_query("""select name from sys.databases""")
        catalogs = [row[0] for row in result]
        print(catalogs)
        return catalogs
