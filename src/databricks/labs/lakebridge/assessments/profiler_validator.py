import os
from dataclasses import dataclass
from duckdb import DuckDBPyConnection

from databricks.labs.lakebridge.assessments.pipeline import PipelineClass

PROFILER_DB_NAME = "profiler_extract.db"


@dataclass(frozen=True)
class ValidationOutcome:
    """A data class that holds the outcome of a table validation check."""

    table: str
    column: str | None
    strategy: str
    outcome: str
    severity: str


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
        return ValidationOutcome(self.table, self.column, self.name, outcome, self.severity)


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
        result = connection.execute(f"SELECT COUNT(*) FROM {self.table}").fetchone()
        if result:
            row_count = result[0]
            outcome = "PASS" if row_count > 0 else "FAIL"
        else:
            outcome = "FAIL"
        return ValidationOutcome(self.table, None, self.name, outcome, self.severity)


def get_profiler_extract_path(pipeline_config_path: str) -> str:
    """
    Returns the filesystem path of the profiler extract database.
    input:
       pipeline_config_path: the location of the pipeline definition .yml file
    returns:
       the filesystem path to the profiler extract database
    """
    pipeline_config = PipelineClass.load_config_from_yaml(pipeline_config_path)
    normalized_db_path = os.path.normpath(pipeline_config.extract_folder)
    database_path = f"{normalized_db_path}/{PROFILER_DB_NAME}"
    return database_path


def build_validation_report(
    validations: list[ValidationStrategy], connection: DuckDBPyConnection
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
