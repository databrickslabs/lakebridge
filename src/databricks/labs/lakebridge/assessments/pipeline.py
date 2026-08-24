import json
import logging
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from subprocess import PIPE, STDOUT, Popen

import duckdb
import yaml
from databricks.labs.blueprint.paths import read_text

from databricks.labs.lakebridge import __version__ as lakebridge_version
from databricks.labs.lakebridge.assessments.profiler_config import PipelineConfig, Step
from databricks.labs.lakebridge.connections.database_manager import DatabaseConnector, FetchResult
from databricks.labs.lakebridge.resources.assessments.common.duckdb_helpers import connect_to_profiler_db

logger = logging.getLogger(__name__)


def make_profiler_db_filename(platform: str) -> str:
    return f"profiler_extract_{platform}_{lakebridge_version}_{datetime.now(timezone.utc).strftime('%Y%m%d')}.db"


class StepExecutionStatus(str, Enum):
    COMPLETE = "COMPLETE"
    ERROR = "ERROR"
    ERROR_FATAL = "ERROR_FATAL"
    SKIPPED = "SKIPPED"
    ABSENT = "ABSENT"


class DuckDBDDLError(RuntimeError):
    """Raised when applying or writing through a SQL step's DuckDB schema contract fails."""


@dataclass
class StepExecutionResult:
    step_name: str
    status: StepExecutionStatus
    error_message: str | None = None


class PipelineClass:
    def __init__(
        self,
        config: PipelineConfig,
        executor: DatabaseConnector | None,
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

            if result.status == StepExecutionStatus.ERROR_FATAL:
                error_msg = f"Pipeline execution failed due to error in DDL step: {result.step_name}"
                if result.error_message:
                    error_msg += f" - {result.error_message}"
                logger.error(error_msg)
                raise RuntimeError(error_msg)

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
            self._dispatch_step(step)
            return StepExecutionResult(step_name=step.name, status=StepExecutionStatus.COMPLETE)
        except DuckDBDDLError as e:
            return StepExecutionResult(
                step_name=step.name,
                status=StepExecutionStatus.ERROR_FATAL,
                error_message=str(e),
            )
        except (RuntimeError, ConnectionError) as e:
            if step.optional:
                status = StepExecutionStatus.ABSENT
            elif step.type == "source_ddl":
                status = StepExecutionStatus.ERROR_FATAL
            else:
                status = StepExecutionStatus.ERROR
            return StepExecutionResult(step_name=step.name, status=status, error_message=str(e))

    def _dispatch_step(self, step: Step) -> None:
        match step.type:
            case "sql":
                self._execute_sql_step(step)
            case "source_ddl":
                self._execute_source_ddl_step(step)
            case "python":
                self._execute_python_step(step)
            case _:
                raise RuntimeError(f"Unsupported step type: {step.type}")

    def _log_step_result(self, result: StepExecutionResult):
        match result.status:
            case StepExecutionStatus.ERROR | StepExecutionStatus.ERROR_FATAL:
                logger.error(f"Step {result.step_name} failed with error: {result.error_message}")
            case StepExecutionStatus.ABSENT:
                logger.warning(f"Optional step {result.step_name} failed and was tolerated: {result.error_message}")
            case StepExecutionStatus.SKIPPED:
                logger.info(f"Step {result.step_name} was skipped.")
            case StepExecutionStatus.COMPLETE:
                logger.info(f"Step {result.step_name} has completed successfully.")

    def _execute_sql_step(self, step: Step):
        logging.debug(f"Reading query from file: {step.extract_source}")
        query = read_text(Path(step.extract_source))
        logging.debug(f"Query for step '{step.name}' will be: {query}")

        if self.executor is None:
            logging.error("Database executor is not set.")
            raise RuntimeError("Database executor is not set.")

        if self.executor.supports_streaming():
            # Warning: in this mode writing the step data may not be atomic.
            self._stream_sql_step(step, query)
            return

        logging.info(f"Executing query for step: {step.name}")
        result = self.executor.fetch(query)
        self._save_to_db(result, step)

    def _stream_sql_step(self, step: Step, query: str) -> None:
        if self.executor is None:
            raise RuntimeError("Database executor is not set.")

        logging.info(f"Starting query for step: {step.name}")
        try:
            with connect_to_profiler_db(self._db_path) as conn:
                total_rows = self._write_stream(conn, step, query)
        except (RuntimeError, ConnectionError):
            raise
        except Exception as e:
            raise DuckDBDDLError(f"DuckDB schema or insert failed for SQL step '{step.name}': {e}") from e

        logging.info(f"Finished streaming query for step: {step.name} (rows={total_rows}, mode={step.mode})")
        logging.debug(f"Data flushed for step: {step.name}")

    def _write_stream(self, conn: duckdb.DuckDBPyConnection, step: Step, query: str) -> int:
        if self.executor is None:
            raise RuntimeError("Database executor is not set.")
        conn.begin()
        self._apply_duckdb_ddl(conn, step)
        total_rows = 0
        for batch in self.executor.stream(query):
            if (batch_size := batch.num_rows) == 0:
                logger.debug(f"Skipping empty batch while streaming results for step: {step.name}")
                continue
            logger.debug(f"Streaming batch of {batch_size} rows for step: {step.name}")
            conn.register("_result_frame", batch)
            conn.execute(f"INSERT INTO {step.name} SELECT * FROM _result_frame")
            conn.unregister("_result_frame")
            total_rows += batch_size
        conn.commit()
        return total_rows

    def _apply_duckdb_ddl(self, conn: duckdb.DuckDBPyConnection, step: Step) -> None:
        if not step.ddl_source:
            raise DuckDBDDLError(f"SQL step '{step.name}' is missing ddl_source")

        logging.debug(f"Reading DDL from file: {step.ddl_source}")
        ddl = read_text(Path(step.ddl_source)).strip()
        if step.mode == "overwrite":
            conn.execute(f"DROP TABLE IF EXISTS {step.name}")
            conn.execute(ddl)
            logging.debug(f"Recreated table '{step.name}' from DDL")
        elif not self._table_exists(conn, step.name):
            conn.execute(ddl)
            logging.debug(f"Created table '{step.name}' from DDL")
        else:
            logging.debug(f"Table '{step.name}' already exists; skipping DDL in append mode")

    def _execute_source_ddl_step(self, step: Step):
        """Run a no-result DDL statement against the *source* database (one statement per file).

        Distinct from ``ddl`` (which targets the local DuckDB extract) and from ``sql``
        (which expects a result set: ``DatabaseConnector.fetch`` calls ``fetchall()`` and
        raises on statements that return no rows). Used to create/drop source-side
        views or objects that subsequent ``sql`` steps depend on.
        """
        logging.debug(f"Reading source_ddl script from file: {step.extract_source}")
        content = read_text(Path(step.extract_source)).strip()

        if self.executor is None:
            logging.error("Database executor is not set.")
            raise RuntimeError("Database executor is not set.")

        if not content or all(line.strip().startswith("--") for line in content.split("\n")):
            logging.warning(f"source_ddl step '{step.name}' has no statement in {step.extract_source}")
            return

        logging.info(f"Executing source_ddl step '{step.name}' on source")
        self.executor.fetch(content)

    def _execute_python_step(self, step: Step):
        logging.debug(f"Executing Python script: {step.extract_source}")
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

    def _save_to_db(self, result: FetchResult, step: Step) -> None:
        row_count = len(result.rows)
        logging.info(f"Query for step '{step.name}' returned {row_count} rows.")

        try:
            with connect_to_profiler_db(self._db_path) as conn:
                conn.begin()
                self._apply_duckdb_ddl(conn, step)
                if result.rows:
                    _result_frame = result.to_df()
                    conn.execute(f"INSERT INTO {step.name} SELECT * FROM _result_frame")
                conn.commit()
        except Exception as e:
            if isinstance(e, DuckDBDDLError):
                raise
            raise DuckDBDDLError(f"DuckDB schema or insert failed for SQL step '{step.name}': {e}") from e

        if not result.rows:
            logging.warning(
                f"Query for step '{step.name}' returned 0 rows. " "Created the typed table and skipped data insertion."
            )
        else:
            logging.info(f"Successfully processed {row_count} rows for table '{step.name}'.")
        logger.debug(f"Flushed committed data to database for step: {step.name}")

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
