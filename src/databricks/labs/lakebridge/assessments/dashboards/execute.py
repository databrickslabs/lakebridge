import logging
import os
import sys
from collections.abc import Sequence
from importlib import resources
from importlib.abc import Traversable
from pathlib import Path

import duckdb
import yaml
from pyspark.sql import SparkSession
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


def main(*argv: str) -> None:
    """Lakeview Jobs task entry point: profiler_dashboards"""
    initialize_logging()

    logger.debug(f"Arguments received: {argv}")
    assert len(sys.argv) == 4, f"Invalid number of arguments: {len(sys.argv)}"
    catalog_name = sys.argv[0]
    schema_name = sys.argv[1]
    extract_location = sys.argv[2]
    source_tech = sys.argv[3]
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
    # TODO: Verify this, I don't think it works? (These files are part of the test resources.)
    schema_def = resources.files(assessment_resources).joinpath(f"{source_tech}_schema_def.yml")
    tables = _get_extract_tables(schema_def)
    try:
        with duckdb.connect(database=extract_location) as duck_conn, resources.as_file(schema_def) as schema_def_path:
            for table_info in tables:
                # Ensure that the table contains data
                empty_check = EmptyTableValidationCheck(table_info[2])
                validation_checks.append(empty_check)

                # Ensure that the table conforms to the expected schema
                schema_check = ExtractSchemaValidationCheck(
                    table_info[0],
                    table_info[1],
                    source_tech=source_tech,
                    extract_path=extract_location,
                    schema_path=str(schema_def_path),
                )
                validation_checks.append(schema_check)
            report = build_validation_report(validation_checks, duck_conn)
    except duckdb.IOException as e:
        logger.exception(f"Could not access the profiler extract: '{extract_location}'.")
        raise e
    except Exception as e:
        logger.exception(f"Unable to validate the profiler extract: '{extract_location}'.")
        raise e

    # Save validation report to table
    report_df = build_validation_report_dataframe(validation_checks, duck_conn)
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
        except RuntimeError as e:
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
    """
    try:
        with duckdb.connect(database=extract_location, read_only=True) as duck_conn:
            query = f"SELECT * FROM {source_table_name}"
            pdf = duck_conn.execute(query).df()
            # Save table as a managed Delta table in Unity Catalog
            logger.info(f"Saving profiler table '{target_table_name}' to Unity Catalog.")
            spark = SparkSession.builder.getOrCreate()
            df = spark.createDataFrame(pdf)
            df.write.format("delta").mode("overwrite").saveAsTable(target_table_name)
    except duckdb.CatalogException as e:
        logger.error(f"Could not find source table '{source_table_name}' in profiler extract: {e}")
        raise duckdb.CatalogException(f"Could not find source table '{source_table_name}' in profiler extract.") from e
    except duckdb.IOException as e:
        logger.error(f"Could not access the profiler extract: '{extract_location}': {e}")
        raise duckdb.IOException(f"Could not access the profiler extract: '{extract_location}'.") from e
    except Exception as e:
        logger.error(f"Unable to ingest table '{source_table_name}' from profiler extract: {e}")
        raise e


if __name__ == "__main__":
    # Ensure that the ingestion job is being run on a Databricks cluster
    if "DATABRICKS_RUNTIME_VERSION" not in os.environ:
        raise SystemExit("The Lakebridge profiler ingestion job is only intended to run in a Databricks Runtime.")
    main(*sys.argv)
