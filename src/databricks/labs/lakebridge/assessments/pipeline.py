import json
import logging
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from subprocess import PIPE, Popen, STDOUT

import duckdb
import yaml

from databricks.labs.blueprint.paths import read_text
from databricks.labs.lakebridge import __version__ as lakebridge_version
from databricks.labs.lakebridge.assessments.profiler_config import PipelineConfig, Step
from databricks.labs.lakebridge.connections.database_manager import DatabaseManager, FetchResult

logger = logging.getLogger(__name__)


def make_profiler_db_filename(platform: str) -> str:
    return f"profiler_extract_{platform}_{lakebridge_version}_{datetime.now(timezone.utc).strftime('%Y%m%d')}.db"


class StepExecutionStatus(str, Enum):
    COMPLETE = "COMPLETE"
    ERROR = "ERROR"
    SKIPPED = "SKIPPED"


@dataclass
class StepExecutionResult:
    step_name: str
    status: StepExecutionStatus
    error_message: str | None = None


class PipelineClass:
    def __init__(
        self,
        config: PipelineConfig,
        executor: DatabaseManager | None,
        db_path: Path,
        cred_file_path: Path,
    ):
        self.config = config
        self.executor = executor
        self._db_path = db_path.expanduser()
        self._create_dir(self._db_path.parent)
        self._cred_file_path = cred_file_path

    def execute(self) -> list[StepExecutionResult]:
        logging.info(f"Pipeline initialized with config: {self.config.name}, version: {self.config.version}")
        execution_results: list[StepExecutionResult] = []

        for step in self.config.steps:
            result = self._process_step(step)
            execution_results.append(result)
            self._log_step_result(result)

            # Fail immediately if a DDL (local DuckDB) or source_ddl (source-side) step failed
            if step.type in {"ddl", "source_ddl"} and result.status == StepExecutionStatus.ERROR:
                error_msg = f"Pipeline execution failed due to error in DDL step: {result.step_name}"
                if result.error_message:
                    error_msg += f" - {result.error_message}"
                logger.error(error_msg)
                raise RuntimeError(error_msg)

        # Check if any non-DDL steps failed
        failed_steps = [r for r in execution_results if r.status == StepExecutionStatus.ERROR]
        if failed_steps:
            error_msg = (
                f"Pipeline execution failed due to errors in steps: {', '.join(r.step_name for r in failed_steps)}"
            )
            logger.error(error_msg)
            raise RuntimeError(error_msg)

        return execution_results

    def _process_step(self, step: Step) -> StepExecutionResult:
        logger.info(f"Executing step: {step.name}")

        if step.flag != "active":
            logging.info(f"Skipping step: {step.name} as it is not active")
            return StepExecutionResult(step_name=step.name, status=StepExecutionStatus.SKIPPED)

        try:
            # Execute based on step type
            match step.type:
                case "sql":
                    self._execute_sql_step(step)
                case "ddl":
                    self._execute_ddl_step(step)
                case "source_ddl":
                    self._execute_source_ddl_step(step)
                case "python":
                    self._execute_python_step(step)
                case _:
                    raise RuntimeError(f"Unsupported step type: {step.type}")

            return StepExecutionResult(step_name=step.name, status=StepExecutionStatus.COMPLETE)
        except RuntimeError as e:
            return StepExecutionResult(step_name=step.name, status=StepExecutionStatus.ERROR, error_message=str(e))

    def _log_step_result(self, result: StepExecutionResult):
        match result.status:
            case StepExecutionStatus.ERROR:
                logger.error(f"Step {result.step_name} failed with error: {result.error_message}")
            case StepExecutionStatus.SKIPPED:
                logger.info(f"Step {result.step_name} was skipped.")
            case StepExecutionStatus.COMPLETE:
                logger.info(f"Step {result.step_name} has completed successfully.")

    def _execute_sql_step(self, step: Step):
        logging.debug(f"Reading query from file: {step.extract_source}")
        query = read_text(Path(step.extract_source))

        if self.executor is None:
            logging.error("DatabaseManager executor is not set.")
            raise RuntimeError("DatabaseManager executor is not set.")

        # Execute the query using the database manager
        logging.info(f"Executing query: {query}")
        try:
            result = self.executor.fetch(query)

            # Save the result to duckdb
            self._save_to_db(result, step.name, str(step.mode))
        except Exception as e:
            logging.error(f"SQL execution failed: {str(e)}")
            raise RuntimeError(f"SQL execution failed: {str(e)}") from e

    def _execute_source_ddl_step(self, step: Step):
        """Run a no-result DDL statement against the *source* database (one statement per file).

        Distinct from ``ddl`` (which targets the local DuckDB extract) and from ``sql``
        (which expects a result set: ``DatabaseManager.fetch`` calls ``fetchall()`` and
        raises on statements that return no rows). Used to create/drop source-side
        views or objects that subsequent ``sql`` steps depend on.
        """
        logging.debug(f"Reading source_ddl script from file: {step.extract_source}")
        content = read_text(Path(step.extract_source)).strip()

        if self.executor is None:
            logging.error("DatabaseManager executor is not set.")
            raise RuntimeError("DatabaseManager executor is not set.")

        if not content or all(line.strip().startswith("--") for line in content.split("\n")):
            logging.warning(f"source_ddl step '{step.name}' has no statement in {step.extract_source}")
            return

        logging.info(f"Executing source_ddl step '{step.name}' on source")
        try:
            self.executor.fetch(content)
        except Exception as e:
            logging.error(f"source_ddl step failed: {str(e)}")
            raise RuntimeError(f"source_ddl step failed: {str(e)}") from e

    def _execute_ddl_step(self, step: Step):
        logging.debug(f"Reading DDL from file: {step.extract_source}")
        ddl = read_text(Path(step.extract_source)).strip()

        logging.info(f"Executing DDL for table '{step.name}'")

        try:
            # TODO: Handle schema evolution
            # Current implementation just checks for table existence;
            # mode logic becomes irrelevant for ddl step.
            with duckdb.connect(self._db_path) as conn:
                conn.begin()
                if not self._table_exists(conn, step.name):
                    conn.execute(ddl)
                    conn.commit()
                    logging.debug(f"Created new table '{step.name}'")
                else:
                    logging.debug(f"Table '{step.name}' already exists, skipping DDL execution")
        except Exception as e:
            logging.error(f"DDL execution failed: {str(e)}")
            raise RuntimeError(f"DDL execution failed: {str(e)}") from e

    def _execute_python_step(self, step: Step):
        logging.debug(f"Executing Python script: {step.extract_source}")
        # Run the step script with the labs-managed venv
        logger.info(f"Executing Python script for step '{step.name}' using interpreter: {sys.executable}")
        self._run_python_script(sys.executable, step.extract_source, self._db_path, self._cred_file_path)

    @staticmethod
    def _run_python_script(venv_exec_cmd: str, script_path: str, db_path: Path, credential_config: Path):
        output_lines = []
        try:
            with Popen(
                [
                    venv_exec_cmd,
                    script_path,
                    "--db-path",
                    str(db_path),
                    "--credential-config-path",
                    str(credential_config),
                ],
                stdout=PIPE,
                stderr=STDOUT,
                text=True,
                bufsize=1,
            ) as process:
                if process.stdout is not None:
                    for line in process.stdout:
                        logger.info(line.rstrip())
                        output_lines.append(line)
                process.wait()
        except Exception as e:
            logging.error(f"Python script failed: {str(e)}")
            raise RuntimeError(f"Script execution failed: {str(e)}") from e

        if output_lines:
            try:
                output = json.loads(output_lines[-1])
            except json.JSONDecodeError:
                logging.info("Could not parse script output as JSON.")
                output = {
                    "status": "error",
                    "message": "Could not parse script output as JSON, manually validate the logs.",
                }

            if output.get("status") == "success":
                logging.info(f"Python script completed: {output['message']}")
            else:
                raise RuntimeError(f"Script reported error: {output.get('message', 'Unknown error')}")

        if process.returncode != 0:
            raise RuntimeError(f"Script execution failed with exit code {process.returncode}")

    def _save_to_db(self, result: FetchResult, step_name: str, mode: str):
        # Check row count and log appropriately and skip data insertion if 0 rows
        if not result.rows:
            logging.warning(
                f"Query for step '{step_name}' returned 0 rows. Skipping table creation and data insertion."
            )
            return

        row_count = len(result.rows)
        logging.info(f"Query for step '{step_name}' returned {row_count} rows.")

        with duckdb.connect(self._db_path) as conn:
            # Note: step_name is validated to be SQL-safe by Step.__post_init__
            table_exists = self._table_exists(conn, step_name)
            conn.begin()
            if table_exists and mode == 'overwrite':
                # Table exists and overwrite mode: Truncate then insert within a transaction to preserve existing DDL schema
                _result_frame = result.to_df()
                # Note: step_name is validated to be SQL-safe by Step.__post_init__
                logging.debug(f"Overwriting existing table '{step_name}'")
                conn.execute(f"TRUNCATE {step_name}")
                conn.execute(f"INSERT INTO {step_name} SELECT * FROM _result_frame")
            else:
                if table_exists:
                    # Table exists and append mode: insert into existing table (DuckDB handles type conversion)
                    _result_frame = result.to_df()
                    # Note: step_name is validated to be SQL-safe by Step.__post_init__
                    statement = f"INSERT INTO {step_name} SELECT * FROM _result_frame"
                    logging.debug(f"Appending to existing table '{step_name}'")
                else:
                    # Table doesn't exist: create table with native types from query result
                    # Use DDL steps for explicit type control when needed
                    _result_frame = result.to_df()
                    # Note: step_name is validated to be SQL-safe by Step.__post_init__
                    statement = f"CREATE TABLE {step_name} AS SELECT * FROM _result_frame"
                    logging.debug(f"Creating new table '{step_name}' with native types")

                logging.debug(f"Executing: {statement}")
                conn.execute(statement)

            # Explicit commit before context exit
            conn.commit()
            logging.info(f"Successfully processed {row_count} rows for table '{step_name}'.")

    @staticmethod
    def _table_exists(conn: duckdb.DuckDBPyConnection, table_name: str) -> bool:
        result = conn.execute(
            "SELECT COUNT(*) FROM information_schema.tables WHERE table_name = ?", [table_name]
        ).fetchone()
        return result[0] > 0 if result else False

    @staticmethod
    def _create_dir(dir_path: Path):
        if not Path(dir_path).exists():
            dir_path.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def load_config_from_yaml(file_path: str | Path) -> PipelineConfig:
        with open(file_path, 'r', encoding='utf-8') as file:
            data = yaml.safe_load(file)
        steps = [Step(**step) for step in data['steps']]
        return PipelineConfig(
            name=data['name'],
            version=data['version'],
            steps=steps,
        )
