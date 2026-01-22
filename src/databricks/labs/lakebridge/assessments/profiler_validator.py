import os
from dataclasses import dataclass
from collections.abc import Sequence
from pathlib import Path

import yaml
from duckdb import DuckDBPyConnection, CatalogException, ParserException, Error
from pyspark.sql import DataFrame, SparkSession

from databricks.labs.lakebridge.assessments.pipeline import PipelineClass

PROFILER_DB_NAME = "profiler_extract.db"


class SchemaDefinitionLoadError(Exception):
    """An exception that is raised when a schema definition cannot be loaded."""


class SchemaValidationError(Exception):
    """An exception that is raised when a schema cannot be validated against the source."""


@dataclass(frozen=True)
class ValidationOutcome:
    """A data class that holds the outcome of a table validation check."""

    table: str
    column: str | None
    strategy: str
    outcome: str
    severity: str
    summary: str | None


class ValidationStrategy:
    """Abstract class for validating a Profiler table"""

    def validate(self, connection: DuckDBPyConnection) -> ValidationOutcome:
        raise NotImplementedError


class NullValidationCheck(ValidationStrategy):
    """Concrete class for validating null values in a profiler table"""

    def __init__(self, table, column, severity="WARN"):
        self.name = self.__class__.__name__
        self.table = table
        self.column = column
        self.severity = severity

    def validate(self, connection: DuckDBPyConnection) -> ValidationOutcome:
        """
        Validates that a column does not contain null values.
        input:
          connection: a DuckDB connection object
        """
        result = connection.execute(f"SELECT COUNT(*) FROM {self.table} WHERE {self.column} IS NULL").fetchone()
        if result:
            row_count = result[0]
            outcome = "FAIL" if row_count > 0 else "PASS"
        else:
            outcome = "FAIL"
        return ValidationOutcome(self.table, self.column, self.name, outcome, self.severity, None)


class EmptyTableValidationCheck(ValidationStrategy):
    """Concrete class for validating empty tables from a profiler run."""

    def __init__(self, table, severity="WARN"):
        self.name = self.__class__.__name__
        self.table = table
        self.severity = severity

    def validate(self, connection) -> ValidationOutcome:
        """Validates that a table is not empty.
        input:
          connection: a DuckDB connection object
        returns:
          a ValidationOutcome object
        """
        try:
            result = connection.execute(f"SELECT COUNT(*) FROM {self.table}").fetchone()
            if result:
                row_count = result[0]
                outcome = "PASS" if row_count > 0 else "FAIL"
            else:
                outcome = "FAIL"
        except (CatalogException, ParserException):
            return ValidationOutcome(self.table, None, self.name, "FAIL", self.severity, "Table not found.")
        return ValidationOutcome(self.table, None, self.name, outcome, self.severity, None)


class ExtractSchemaValidationCheck(ValidationStrategy):
    """Concrete class for validating the schema of a profiler extract."""

    def __init__(
        self, schema: str, table: str, source_tech: str, extract_path: str, schema_path: str, severity: str = "WARN"
    ) -> None:
        self.name = self.__class__.__name__
        self.schema = schema
        self.table = table
        self.source_tech = source_tech
        self.extract_path = extract_path
        self.schema_path = schema_path
        self.severity = severity

    def _load_schema_definition(self) -> dict:
        """
        Loads a schema definition file from a local path.
        An `AssertionError` is raised if the schema does not match the expected source tech type.
        """
        try:
            with open(self.schema_path, "r", encoding="UTF-8") as f:
                schema_definition = yaml.safe_load(f)
        except FileNotFoundError as e:
            raise SchemaDefinitionLoadError(f"Schema definition file not found: '{self.schema_path}'") from e
        except (yaml.YAMLError, OSError) as e:
            raise SchemaDefinitionLoadError(f"Error while loading schema definition file: '{e}'") from e

        # Ensure that the correct schema definition was loaded
        assert (
            schema_definition["source_tech"].lower() == self.source_tech.lower()
        ), f"Incorrect schema definition type for source tech '{self.source_tech}'"

        return schema_definition

    def validate(self, connection) -> ValidationOutcome:
        """Validates that a table conforms to the expected schema.
        input:
          connection: a DuckDB connection object
        returns:
          a ValidationOutcome object
        """

        # First, load the table info from the schema definition YAML file
        schema_definition = self._load_schema_definition()

        # Load the table name and column info
        try:
            expected_columns = schema_definition["schemas"][self.schema]["tables"][self.table]["columns"]
        except KeyError as e:
            raise SchemaValidationError(
                f"Schema '{self.schema}' or table '{self.table}' could not be found in the schema definition."
            ) from e

        # Validate that:
        # a) the table exists in the profiler extract database
        # b) the columns for the table exist
        column_info_query = f"""
            SELECT column_name, data_type
              FROM information_schema.columns
             WHERE table_schema = '{self.schema}'
               AND table_name = '{self.table}'
        """
        try:
            result = connection.execute(column_info_query).fetchall()
        except (CatalogException, ParserException, Error) as e:
            raise SchemaValidationError(
                f"Could not query column information for table '{self.schema}.{self.table}' - {e}"
            ) from e

        # compare the extract schema with the schema definition
        if not result:
            return ValidationOutcome(
                f"{self.schema}.{self.table}",
                None,
                self.name,
                "FAIL",
                self.severity,
                "Table not found in the profiler extract",
            )

        extract_columns = dict(result)
        for col in expected_columns:
            expected_col_name = col["name"]
            expected_data_type = col["type"]

            # Column must be present in the extracted table
            if expected_col_name not in extract_columns:
                return ValidationOutcome(
                    f"{self.schema}.{self.table}",
                    expected_col_name,
                    self.name,
                    "FAIL",
                    self.severity,
                    "Column does not exist",
                )

            extracted_col_type = extract_columns[expected_col_name]
            if expected_data_type != extracted_col_type:
                return ValidationOutcome(
                    f"{self.schema}.{self.table}",
                    expected_col_name,
                    self.name,
                    "FAIL",
                    self.severity,
                    "Unexpected column data type",
                )

        return ValidationOutcome(
            f"{self.schema}.{self.table}", None, self.name, "PASS", self.severity, "All columns match expected schema"
        )


def get_profiler_extract_path(pipeline_config_path: Path) -> Path:
    """
    Returns the filesystem path of the profiler extract database.
    input:
       pipeline_config_path: the location of the pipeline definition .yml file
    returns:
       the filesystem path to the profiler extract database
    """
    pipeline_config = PipelineClass.load_config_from_yaml(pipeline_config_path)
    normalized_db_path = os.path.normpath(os.path.expanduser(pipeline_config.extract_folder))
    database_path = Path(normalized_db_path) / PROFILER_DB_NAME
    return database_path


def build_validation_report(
    validations: Sequence[ValidationStrategy], connection: DuckDBPyConnection
) -> list[ValidationOutcome]:
    """
    Builds a list of ValidationOutcomes from list of validation checks.
    input:
      validations: a list of ValidationStrategy objects
      connection: a DuckDB connection object
    returns: a list of ValidationOutcomes
    """
    validation_report = []
    for validation in validations:
        validation_report.append(validation.validate(connection))
    return validation_report


def build_validation_report_dataframe(
    validations: Sequence[ValidationStrategy], connection: DuckDBPyConnection
) -> DataFrame:
    """
    Builds a list of ValidationOutcomes from list of validation checks.
    input:
      validations: a list of ValidationStrategy objects
      connection: a DuckDB connection object
    returns: a list of ValidationOutcomes
    """
    validation_report = []
    for validation in validations:
        result = validation.validate(connection)
        validation_report.append(
            (result.table, result.column, result.strategy, result.outcome, result.severity, result.summary)
        )
    spark = SparkSession.builder.getOrCreate()
    schema = "table STRING, column STRING, strategy STRING, outcome STRING, severity STRING, summary STRING"
    return spark.createDataFrame(validation_report, schema)
