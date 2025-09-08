import logging
from io import StringIO

from databricks.labs.lsql.backends import SqlBackend
from databricks.labs.lakebridge.config import TranspileConfig, ValidationResult
from databricks.sdk.errors.base import DatabricksError

logger = logging.getLogger(__name__)


class Validator:
    """
    The Validator class is used to validate SQL queries.
    """

    def __init__(self, sql_backend: SqlBackend):
        self._sql_backend = sql_backend

    def validate_format_result(self, config: TranspileConfig, sql_text: str) -> ValidationResult:
        """
        Validates the SQL query and formats the result.

        This function validates the SQL query based on the provided configuration. If the query is valid,
        it appends a semicolon to the end of the query. If the query is not valid, it formats the error message.

        Parameters:
        - config (MorphConfig): The configuration for the validation.
        - sql_text (str): The SQL query to be validated.

        Returns:
        - tuple: A tuple containing the result of the validation and the exception message (if any).
        """
        logger.debug(f"Validating query with catalog {config.catalog_name} and schema {config.schema_name}")
        (is_valid, exception_type, exception_msg) = self._query(
            self._sql_backend,
            sql_text,
            config.catalog_name,
            config.schema_name,
        )
        # Some errors doesn't return the query test alon with the error message so need to handle those separately
        static_errors_lkp = ["[UNRESOLVED_ROUTINE]", "[UNRESOLVED_COLUMN.WITHOUT_SUGGESTION]"]
        if is_valid:
            result = sql_text
            if exception_type is not None:
                exception_msg = f"[{exception_type.upper()}]: {exception_msg}"
        else:
            query = ""
            if any(err in str(exception_msg) for err in static_errors_lkp):
                query = sql_text
            buffer = StringIO()
            buffer.write("-------------- Exception Start-------------------\n")
            buffer.write("/*\n")
            buffer.write(str(exception_msg))
            buffer.write("\n*/\n")
            buffer.write(query)
            buffer.write("\n ---------------Exception End --------------------\n")

            result = buffer.getvalue()

        return ValidationResult(result, exception_msg)

    def _query(
        self, sql_backend: SqlBackend, query: str, catalog: str, schema: str
    ) -> tuple[bool, str | None, str | None]:
        """
        Validate a given SQL query using the provided SQL backend

        Parameters:
        - query (str): The SQL query to be validated.
        - sql_backend (SqlBackend): The SQL backend to be used for validation.

        Returns:
        - tuple: A tuple containing a boolean indicating whether the query is valid or not,
        and a string containing a success message or an exception message.
        """
        # When variables is mentioned Explain fails we need way to replace them before explain is executed.
        explain_query = f'EXPLAIN {query.replace("${", "`{").replace("}", "}`").replace("``", "`")}'
        try:
            rows = list(sql_backend.fetch(explain_query, catalog=catalog, schema=schema))
            if not rows:
                return False, "error", "No results returned from explain query."

            if "Error occurred during query planning" in rows[0].asDict().get("plan", ""):
                error_details = rows[1].asDict().get("plan", "Unknown error.") if len(rows) > 1 else "Unknown error."
                raise DatabricksError(error_details)
            return True, None, None
        except DatabricksError as dbe:
            err_msg = str(dbe)
            if "[PARSE_SYNTAX_ERROR]" in err_msg:
                logger.debug(f"Syntax Exception : NOT IGNORED. Flag as syntax error: {err_msg}")
                return False, "error", err_msg
            if "[UNRESOLVED_ROUTINE]" in err_msg:
                logger.debug(f"Analysis Exception : NOT IGNORED: Flag as Function Missing error {err_msg}")
                return False, "error", err_msg
            if "[TABLE_OR_VIEW_NOT_FOUND]" in err_msg or "[TABLE_OR_VIEW_ALREADY_EXISTS]" in err_msg:
                logger.debug(f"Analysis Exception : IGNORED: {err_msg}")
                return True, "warning", err_msg
            if "Hive support is required to CREATE Hive TABLE (AS SELECT).;" in err_msg:
                logger.debug(f"Analysis Exception : IGNORED: {err_msg}")
                return True, "warning", err_msg

            logger.debug(f"Unknown Exception: {err_msg}")
            return False, "error", err_msg
