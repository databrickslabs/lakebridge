import logging
import os
import sys
from collections.abc import Sequence
from importlib import resources
from importlib.abc import Traversable
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd
import yaml
from pyspark.sql import SparkSession
from pyspark.sql.types import (
    BinaryType,
    BooleanType,
    DateType,
    DoubleType,
    LongType,
    StringType,
    StructField,
    StructType,
    TimestampType,
)
from yaml.parser import ParserError
from yaml.scanner import ScannerError

import databricks.labs.lakebridge.resources.assessments as assessment_resources
from databricks.labs.lakebridge.assessments.profiler_validator import (
    EmptyTableValidationCheck,
    build_validation_report,
    ExtractSchemaValidationCheck,
    build_validation_report_dataframe,
)
from databricks.labs.lakebridge import initialize_logging

logger = logging.getLogger(__name__)

# Columns that contain potentially sensitive data and require a UC column comment
# and tag after ingestion. Key: table_name, Value: {col_name: comment}.
_SENSITIVE_COLUMNS: dict[str, dict[str, str]] = {
    "td_dbql_core_info_extract": {
        "SQLTextInfo": (
            "CONFIDENTIAL: Raw SQL query text captured from Teradata DBQL. "
            "May contain proprietary business logic, table/column names, filter predicates, "
            "and sensitive identifiers. Govern access via Unity Catalog column masking policies."
        )
    }
}


def _is_truthy_env(var_name: str, default: bool = False) -> bool:
    raw = os.getenv(var_name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def _sqltext_sensitivity_tag_value() -> str:
    """
    Returns the UC tag value to use for SQLTextInfo sensitivity tagging.

    Default is 'pii' to align with common workspace tag policies.
    Can be overridden via LAKEBRIDGE_SQLTEXT_SENSITIVITY_TAG.
    """
    value = os.getenv("LAKEBRIDGE_SQLTEXT_SENSITIVITY_TAG", "pii").strip().lower()
    return value or "pii"


def _apply_sensitive_column_metadata(spark: SparkSession, fq_table_name: str, table_name: str) -> None:
    """
    Applies a column COMMENT and a 'sensitivity' UC tag to any columns declared
    in _SENSITIVE_COLUMNS for the given table.  Both operations are best-effort —
    failures are logged as warnings so that a missing governance permission does
    not abort the entire ingestion job.
    """
    sensitivity_tag_value = _sqltext_sensitivity_tag_value()
    for col_name, comment in _SENSITIVE_COLUMNS.get(table_name, {}).items():
        safe_comment = comment.replace("'", "\\'")
        try:
            spark.sql(f"ALTER TABLE {fq_table_name} ALTER COLUMN {col_name} COMMENT '{safe_comment}'")
            logger.info(f"Applied sensitivity comment to '{fq_table_name}.{col_name}'.")
        except Exception as e:  # noqa: BLE001  # pylint: disable=broad-exception-caught
            logger.warning(f"Could not set column comment on '{fq_table_name}.{col_name}': {e}")
        try:
            spark.sql(
                f"ALTER TABLE {fq_table_name} ALTER COLUMN {col_name} "
                f"SET TAGS ('sensitivity' = '{sensitivity_tag_value}')"
            )
            logger.info(f"Applied sensitivity tag to '{fq_table_name}.{col_name}'.")
        except Exception as e:  # noqa: BLE001  # pylint: disable=broad-exception-caught
            logger.warning(f"Could not set column tag on '{fq_table_name}.{col_name}': {e}")


def _apply_sensitive_column_mask(spark: SparkSession, fq_table_name: str, table_name: str) -> None:
    """
    Best-effort masking policy application for sensitive columns.

    Feature flag:
      - LAKEBRIDGE_ENABLE_SQLTEXT_MASK=true|false (default: false)

    Optional behavior:
      - LAKEBRIDGE_SQLTEXT_MASK_BYPASS_GROUP=<group-name>
        Members of this account group see unmasked SQL text.
        If unset/empty, all users see masked SQL text.
    """
    if not _is_truthy_env("LAKEBRIDGE_ENABLE_SQLTEXT_MASK", default=False):
        return

    # Currently this policy is only relevant for the DBQL SQL text column.
    if table_name != "td_dbql_core_info_extract":
        return

    parts = fq_table_name.split(".")
    if len(parts) != 3:
        logger.warning(f"Skipping SQL text masking for unexpected table format: '{fq_table_name}'")
        return
    catalog_name, schema_name, _ = parts
    function_name = f"{catalog_name}.{schema_name}.mask_sql_textinfo"

    bypass_group = os.getenv("LAKEBRIDGE_SQLTEXT_MASK_BYPASS_GROUP", "data-governance-admins").strip()
    if bypass_group:
        safe_group = bypass_group.replace("'", "\\'")
        function_sql = (
            f"CREATE FUNCTION IF NOT EXISTS {function_name}(v STRING) "
            "RETURNS STRING "
            f"RETURN CASE WHEN is_account_group_member('{safe_group}') THEN v ELSE '[REDACTED_SQL_TEXT]' END"
        )
    else:
        function_sql = (
            f"CREATE FUNCTION IF NOT EXISTS {function_name}(v STRING) " "RETURNS STRING " "RETURN '[REDACTED_SQL_TEXT]'"
        )

    try:
        spark.sql(function_sql)
        logger.info(f"Created/verified SQL masking function '{function_name}'.")
    except Exception as e:  # noqa: BLE001  # pylint: disable=broad-exception-caught
        logger.warning(f"Could not create masking function '{function_name}': {e}")
        return

    try:
        spark.sql(f"ALTER TABLE {fq_table_name} ALTER COLUMN SQLTextInfo SET MASK {function_name}")
        logger.info(f"Applied SQL text mask on '{fq_table_name}.SQLTextInfo'.")
    except Exception as e:  # noqa: BLE001  # pylint: disable=broad-exception-caught
        logger.warning(f"Could not apply SQL text mask on '{fq_table_name}.SQLTextInfo': {e}")


def _normalize_text(value: Any) -> Any:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, str):
        return value.encode("utf-8", errors="replace").decode("utf-8")
    return value


def _normalize_dataframe_text(pdf: pd.DataFrame) -> pd.DataFrame:
    """
    Ensure object columns can be safely serialized as UTF-8 for Spark ingestion.
    """
    for column in pdf.columns:
        if pdf[column].dtype == "object":
            pdf[column] = pdf[column].map(_normalize_text)
    return pdf


def _spark_type_from_duckdb(duckdb_type: str) -> Any:
    normalized = duckdb_type.upper()
    if normalized in {"BOOLEAN", "BOOL"}:
        return BooleanType()
    if normalized in {"TINYINT", "SMALLINT", "INTEGER", "INT", "BIGINT", "HUGEINT", "UBIGINT"}:
        return LongType()
    if normalized in {"FLOAT", "REAL", "DOUBLE", "DECIMAL", "NUMERIC"}:
        return DoubleType()
    if normalized.startswith("TIMESTAMP"):
        return TimestampType()
    if normalized == "DATE":
        return DateType()
    if normalized in {"BLOB", "BYTEA"}:
        return BinaryType()
    return StringType()


def _spark_schema_from_duckdb(duck_conn: duckdb.DuckDBPyConnection, source_table_name: str) -> StructType:
    table_parts = source_table_name.split(".")
    if len(table_parts) == 3:
        _, table_schema, table_name = table_parts
    elif len(table_parts) == 2:
        table_schema, table_name = table_parts
    elif len(table_parts) == 1:
        table_schema, table_name = "main", table_parts[0]
    else:
        raise ValueError(
            f"Unexpected source table format: '{source_table_name}'. Expected <catalog>.<schema>.<table> "
            f"or <schema>.<table>."
        )

    query = """
        SELECT column_name, data_type
        FROM information_schema.columns
        WHERE table_schema = ? AND table_name = ?
        ORDER BY ordinal_position
    """
    columns = duck_conn.execute(query, [table_schema, table_name]).fetchall()
    if not columns:
        raise ValueError(f"No columns found in profiler extract for source table '{source_table_name}'.")

    fields = [
        StructField(str(col_name), _spark_type_from_duckdb(str(data_type)), True) for col_name, data_type in columns
    ]
    return StructType(fields)


class ExtractIngestionError(Exception):
    """Raised when the profiler extract ingestion fails due to unexpected errors."""


def main(*argv: str) -> None:
    """Lakeview Jobs task entry point: profiler_dashboards"""
    initialize_logging()

    logger.debug(f"Arguments received: {argv}")

    assert len(sys.argv) == 5, f"Invalid number of arguments: {len(sys.argv)}"
    logger.info(f"Received the following inputs: {', '.join(sys.argv)}")

    catalog_name = sys.argv[1]
    schema_name = sys.argv[2]
    extract_location = sys.argv[3]
    source_tech = sys.argv[4]

    logger.info(f"Validating {source_tech} profiler extract located at '{extract_location}'.")
    valid_extract = _validate_profiler_extract(catalog_name, schema_name, extract_location, source_tech)
    if valid_extract:
        _ingest_profiler_tables(catalog_name, schema_name, extract_location)
    else:
        raise ValueError("Corrupt or invalid profiler extract.")


def _get_extract_tables(schema_def_path: Path | Traversable) -> Sequence[tuple[str, str, str]]:
    """
    Given a schema definition file for a source technology, returns a list of table info tuples:
    (schema_name, table_name, fully_qualified_name)
    """
    # First, load the schema definition file
    try:
        with schema_def_path.open(mode="r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except (ParserError, ScannerError) as e:
        raise ValueError(f"Could not read extract schema definition '{schema_def_path}': {e}") from e
    except FileNotFoundError as e:
        raise FileNotFoundError(f"Schema definition not found: {schema_def_path}") from e
    # Iterate through the defined schemas and build a list of
    # table info tuples: (schema_name, table_name, fully_qualified_name)
    extracted_tables: list[tuple[str, str, str]] = []
    for schema_name, schema_def in data.get("schemas", {}).items():
        tables = schema_def.get("tables", {})
        for table_name in tables.keys():
            fq_name = f"{schema_name}.{table_name}"
            extracted_tables.append((schema_name, table_name, fq_name))

    return extracted_tables


def _validate_profiler_extract(
    target_catalog_name: str, target_schema_name: str, extract_location: str, source_tech: str
) -> bool:
    logger.info("Validating the profiler extract file.")
    validation_checks: list[EmptyTableValidationCheck | ExtractSchemaValidationCheck] = []
    schema_def = _resolve_schema_definition(source_tech)
    tables = _get_extract_tables(schema_def)
    try:
        with duckdb.connect(database=extract_location) as duck_conn, resources.as_file(schema_def) as schema_def_path:
            for table_info in tables:

                # Ensure that the table contains data
                empty_check = EmptyTableValidationCheck(table_info[2])

                # Ensure that the table conforms to the expected schema
                schema_check = ExtractSchemaValidationCheck(
                    table_info[0],
                    table_info[1],
                    source_tech=source_tech,
                    extract_path=extract_location,
                    schema_path=str(schema_def_path),
                )
                validation_checks += [empty_check, schema_check]

            report = build_validation_report(validation_checks, duck_conn)
            report_df = build_validation_report_dataframe(validation_checks, duck_conn)
    except duckdb.IOException as e:
        logger.exception(f"Could not access the profiler extract: '{extract_location}'.")
        raise e
    except Exception as e:
        logger.exception(f"Unable to validate the profiler extract: '{extract_location}'.")
        raise e

    # Save validation report to table
    validation_report_table = f"{target_catalog_name}.{target_schema_name}.validation_report"
    logger.info(f"Saving extract validation report to '{validation_report_table}' to Unity Catalog.")
    report_df.write.format("delta").mode("overwrite").saveAsTable(validation_report_table)

    if len(report) > 0:
        report_errors = list(filter(lambda x: x.outcome == "FAIL" and x.severity == "ERROR", report))
        num_errors = len(report_errors)
        logger.info(f"There are {num_errors} validation errors in the profiler extract.")
    else:
        raise ValueError("Profiler extract validation report is empty.")
    return num_errors == 0


def _resolve_schema_definition(source_tech: str) -> Traversable:
    """
    Resolve schema definition file for a profiler source technology.

    Preferred path: <source>_schema_def.yml
    Backward compatible path: validation/<source>_extract_schema.yml
    """
    root = resources.files(assessment_resources)
    direct_schema = root.joinpath(f"{source_tech}_schema_def.yml")
    if direct_schema.is_file():
        return direct_schema

    validation_schema = root.joinpath("validation").joinpath(f"{source_tech}_extract_schema.yml")
    if validation_schema.is_file():
        return validation_schema

    raise FileNotFoundError(
        f"Schema definition not found for source '{source_tech}'. "
        f"Checked '{source_tech}_schema_def.yml' and 'validation/{source_tech}_extract_schema.yml'."
    )


def _ingest_profiler_tables(catalog_name: str, schema_name: str, extract_location: str) -> None:
    try:
        with duckdb.connect(database=extract_location) as duck_conn:
            tables_to_ingest = duck_conn.execute("SHOW ALL TABLES").fetchall()
    except duckdb.IOException as e:
        logger.error(f"Could not access the profiler extract: '{extract_location}': {e}")
        raise duckdb.IOException(f"Could not access the profiler extract: '{extract_location}'.") from e
    except Exception as e:
        logger.error(f"Unable to read tables from profiler extract: '{extract_location}': {e}")
        raise e

    if len(tables_to_ingest) == 0:
        raise ValueError("Profiler extract contains no tables.")

    successful_tables = []
    unsuccessful_tables = []
    for source_table in tables_to_ingest:
        try:
            fq_source_table_name = f"{source_table[0]}.{source_table[1]}.{source_table[2]}"
            fq_delta_table_name = f"{catalog_name}.{schema_name}.{source_table[2]}"
            logger.info(f"Ingesting profiler table: '{fq_source_table_name}'")
            _ingest_table(extract_location, fq_source_table_name, fq_delta_table_name)
            successful_tables.append(fq_source_table_name)
        except (ValueError, IndexError, TypeError) as e:
            logger.error(f"Failed to construct source and destination table names: {e}")
            unsuccessful_tables.append(source_table)
        except duckdb.Error as e:
            logger.error(f"Failed to ingest table from profiler database: {e}")
            unsuccessful_tables.append(source_table)
        except ExtractIngestionError as e:
            logger.error(f"Unknown error while ingested table from profiler database: {e}")
            unsuccessful_tables.append(source_table)
    logger.info(f"Ingested {len(successful_tables)} tables from profiler extract.")
    logger.info(",".join(successful_tables))

    # Log failed tables if there were errors
    logger.warning(f"Failed to ingest {len(unsuccessful_tables)} tables from profiler extract.")
    logger.warning(",".join(str(t) for t in unsuccessful_tables))


def _ingest_table(extract_location: str, source_table_name: str, target_table_name: str) -> None:
    """
    Ingest a table from a DuckDB profiler extract into a managed Delta table in Unity Catalog.
    After writing, applies sensitivity column comments and UC tags to any columns
    declared in _SENSITIVE_COLUMNS (e.g. SQLTextInfo on td_dbql_core_info_extract).
    """
    # pylint: disable=too-many-try-statements
    try:
        with duckdb.connect(database=extract_location, read_only=True) as duck_conn:
            query = f"SELECT * FROM {source_table_name}"
            pdf = _normalize_dataframe_text(duck_conn.execute(query).df())
            logger.info(f"Saving profiler table '{target_table_name}' to Unity Catalog.")
            spark = SparkSession.builder.getOrCreate()
            if pdf.empty:
                schema = _spark_schema_from_duckdb(duck_conn, source_table_name)
                df = spark.createDataFrame([], schema=schema)
            else:
                df = spark.createDataFrame(pdf)
            df.write.format("delta").mode("overwrite").saveAsTable(target_table_name)
            table_name = source_table_name.split(".")[-1]
            _apply_sensitive_column_metadata(spark, target_table_name, table_name)
            _apply_sensitive_column_mask(spark, target_table_name, table_name)
    except duckdb.CatalogException as e:
        logger.error(f"Could not find source table '{source_table_name}' in profiler extract: {e}")
        raise duckdb.CatalogException(f"Could not find source table '{source_table_name}' in profiler extract.") from e
    except duckdb.IOException as e:
        logger.error(f"Could not access the profiler extract: '{extract_location}': {e}")
        raise duckdb.IOException(f"Could not access the profiler extract: '{extract_location}'.") from e
    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.error(f"Unable to ingest table '{source_table_name}' from profiler extract: {e}")
        raise ExtractIngestionError(f"Unable to ingest table '{source_table_name}' from profiler extract: {e}") from e


if __name__ == "__main__":
    # Ensure that the ingestion job is being run on a Databricks cluster
    if "DATABRICKS_RUNTIME_VERSION" not in os.environ:
        raise SystemExit("The Lakebridge profiler ingestion job is only intended to run in a Databricks Runtime.")
    main(*sys.argv)
