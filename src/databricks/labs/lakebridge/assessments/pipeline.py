import json
import logging
import os
import sys
import venv
import tempfile
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from subprocess import CalledProcessError, PIPE, Popen, STDOUT, run

import duckdb
import yaml

from databricks.labs.blueprint.paths import read_text
from databricks.labs.lakebridge.assessments.profiler_config import PipelineConfig, Step
from databricks.labs.lakebridge.connections.credential_manager import cred_file
from databricks.labs.lakebridge.connections.database_manager import DatabaseManager, FetchResult

logger = logging.getLogger(__name__)

DB_NAME = "profiler_extract.db"


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
    def __init__(self, config: PipelineConfig, executor: DatabaseManager | None):
        self.config = config
        self.executor = executor
        self._db_path_prefix = Path(config.extract_folder).expanduser()
        self._create_dir(self._db_path_prefix)
        self._db_path = str(self._db_path_prefix / DB_NAME)

    def execute(self) -> list[StepExecutionResult]:
        logging.info(f"Pipeline initialized with config: {self.config.name}, version: {self.config.version}")
        execution_results: list[StepExecutionResult] = []

        for step in self.config.steps:
            result = self._process_step(step)
            execution_results.append(result)
            self._log_step_result(result)

            # Fail immediately if DDL step failed
            if step.type == "ddl" and result.status == StepExecutionStatus.ERROR:
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
        credential_config = str(cred_file("lakebridge"))
        venv_path_prefix = Path.home() / ".databricks" / "labs" / "lakebridge_profilers"
        os.makedirs(venv_path_prefix, exist_ok=True)

        # Create a temporary directory for the virtual environment
        # TODO Windows has strict checks on for temp venv cleanup, so will ignore cleanup errors and have it cleaned up later
        with tempfile.TemporaryDirectory(dir=venv_path_prefix, ignore_cleanup_errors=True) as temp_dir:
            venv_dir = Path(temp_dir) / "venv"
            venv_exec_cmd = self._create_venv(venv_dir)

            # Define the paths to the virtual environment's Python and pip executables
            if sys.platform == "win32":
                venv_python = (venv_dir / "Scripts" / "python.exe").resolve()
                venv_pip = (venv_dir / "Scripts" / "pip.exe").resolve()
            else:
                venv_python = (venv_dir / "bin" / "python").resolve()
                venv_pip = (venv_dir / "bin" / "pip").resolve()

            # Log resolved paths
            logger.info(f"Resolved venv_python: {venv_python}")
            logger.info(f"Resolved venv_pip: {venv_pip}")

            logger.info(f"Creating a virtual environment for Python script execution: {venv_dir} for step: {step.name}")
            if step.dependencies:
                self._install_dependencies(venv_exec_cmd, step.dependencies)

            self._run_python_script(venv_exec_cmd, step.extract_source, self._db_path, credential_config)

    @staticmethod
    def _install_dependencies(venv_exec_cmd, dependencies):
        logging.info(f"Installing dependencies: {', '.join(dependencies)}")
        try:
            logging.debug("Upgrading local pip")
            is_debug = logging.getLogger(__name__).isEnabledFor(logging.DEBUG)
            run(
                [
                    venv_exec_cmd,
                    "-m",
                    "pip",
                    "install",
                    "--upgrade",
                    "pip",
                    "--require-virtualenv",
                    "--no-input",
                    "--disable-pip-version-check",
                ],
                check=True,
                capture_output=not is_debug,
                text=True,
            )

            run(
                [
                    venv_exec_cmd,
                    "-m",
                    "pip",
                    "install",
                    *dependencies,
                    "--require-virtualenv",
                    "--no-input",
                    "--disable-pip-version-check",
                ],
                check=True,
                capture_output=not is_debug,
                text=True,
            )
        except CalledProcessError as e:
            # Log detailed output at debug level for troubleshooting
            logging.debug(
                f"Failed to install dependencies (exit code {e.returncode})\n" f"stdout: {e.stdout}\nstderr: {e.stderr}"
            )
            logging.error(f"Failed to install dependencies: {e.stderr}")
            raise RuntimeError(f"Failed to install dependencies: {e.stderr}") from e

    @staticmethod
    def _run_python_script(venv_exec_cmd, script_path, db_path, credential_config):
        output_lines = []
        try:
            with Popen(
                [
                    venv_exec_cmd,
                    str(script_path),
                    "--db-path",
                    db_path,
                    "--credential-config-path",
                    credential_config,
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
            # Check if table exists using information_schema
            # TODO: SQL injection vulnerability - use parameterized query with ?
            table_exists = self._table_exists(conn, step_name)
            conn.begin()
            if table_exists and mode == 'overwrite':
                # Table exists and overwrite mode: Truncate then insert within a transaction to preserve existing DDL schema
                _result_frame = result.to_df()
                # TODO: SQL injection vulnerability - use quote_identifier(step_name)
                logging.debug(f"Overwriting existing table '{step_name}'")
                conn.execute(f"TRUNCATE {step_name}")
                conn.execute(f"INSERT INTO {step_name} SELECT * FROM _result_frame")
            else:
                if table_exists:
                    # Table exists and append mode: insert into existing table (DuckDB handles type conversion)
                    _result_frame = result.to_df()
                    # TODO: SQL injection vulnerability - use quote_identifier(step_name)
                    statement = f"INSERT INTO {step_name} SELECT * FROM _result_frame"
                    logging.debug(f"Appending to existing table '{step_name}'")
                else:
                    # Table doesn't exist: create table with native types from query result
                    # Use DDL steps for explicit type control when needed
                    _result_frame = result.to_df()
                    # TODO: SQL injection vulnerability - use quote_identifier(step_name)
                    statement = f"CREATE TABLE {step_name} AS SELECT * FROM _result_frame"
                    logging.debug(f"Creating new table '{step_name}' with native types")

                logging.debug(f"Executing: {statement}")
                conn.execute(statement)

            # Explicit commit before context exit
            conn.commit()
            logging.info(f"Successfully processed {row_count} rows for table '{step_name}'.")

    @staticmethod
    def _table_exists(conn: duckdb.DuckDBPyConnection, table_name: str) -> bool:
        # TODO: SQL injection vulnerability - use parameterized query with ?
        result = conn.execute(
            f"SELECT COUNT(*) FROM information_schema.tables WHERE table_name = '{table_name}'"
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
            name=data['name'], version=data['version'], extract_folder=data['extract_folder'], steps=steps
        )

    @staticmethod
    def _create_venv(install_path: Path) -> str:
        venv_path = install_path
        # Sadly, some platform-specific variations need to be dealt with:
        #   - Windows venvs do not use symlinks, but rather copies, when populating the venv.
        #   - The library path is different.
        use_symlinks = sys.platform != "win32"

        builder = venv.EnvBuilder(with_pip=True, symlinks=use_symlinks)
        builder.create(venv_path)
        context = builder.ensure_directories(venv_path)
        logger.debug(f"Created virtual environment with context: {context}")
        return context.env_exec_cmd
