from pathlib import Path
from subprocess import run, CalledProcessError, Popen, PIPE, STDOUT, DEVNULL
from dataclasses import dataclass
from enum import Enum

import sys
import os
import venv
import tempfile
import json
import logging
import yaml
import duckdb

from databricks.labs.lakebridge.connections.credential_manager import cred_file

from databricks.labs.lakebridge.assessments.profiler_config import PipelineConfig, Step
from databricks.labs.lakebridge.connections.database_manager import DatabaseManager, FetchResult

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

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
        self.db_path_prefix = Path(config.extract_folder)
        self._create_dir(self.db_path_prefix)

    def execute(self) -> list[StepExecutionResult]:
        logging.info(f"Pipeline initialized with config: {self.config.name}, version: {self.config.version}")
        execution_results: list[StepExecutionResult] = []
        error_flag = False
        for step in self.config.steps:
            logger.info(f"Executing step: {step.name}")
            result = self._process_step(step)
            execution_results.append(result)
            logger.info(f"Step '{step.name}' completed with status: {result.status}")

            # Check step execution status
            if result.status == StepExecutionStatus.ERROR:
                logger.error(f"Step {result.step_name} failed with error: {result.error_message}")
                error_flag = True
            elif result.status == StepExecutionStatus.SKIPPED:
                logger.info(f"Step {result.step_name} was skipped.")
            else:
                logger.info(f"Step {result.step_name} has completed successfully.")

        if error_flag:
            failed_steps = [r for r in execution_results if r.status == StepExecutionStatus.ERROR]
            error_msg = (
                f"Pipeline execution failed due to errors in steps: {', '.join(r.step_name for r in failed_steps)}"
            )
            logger.error(error_msg)
            raise RuntimeError(error_msg)
        return execution_results

    def _process_step(self, step: Step) -> StepExecutionResult:
        if step.flag != "active":
            logging.info(f"Skipping step: {step.name} as it is not active")
            return StepExecutionResult(step_name=step.name, status=StepExecutionStatus.SKIPPED)
        logging.debug(f"Executing step: {step.name}")
        try:
            status = self._execute_step(step)
            return StepExecutionResult(step_name=step.name, status=status)
        except RuntimeError as e:
            return StepExecutionResult(step_name=step.name, status=StepExecutionStatus.ERROR, error_message=str(e))

    def _execute_step(self, step: Step) -> StepExecutionStatus:
        if step.type == "sql":
            logging.info(f"Executing SQL step {step.name}")
            self._execute_sql_step(step)
            return StepExecutionStatus.COMPLETE
        if step.type == "python":
            logging.info(f"Executing Python step {step.name}")
            self._execute_python_step(step)
            return StepExecutionStatus.COMPLETE
        logging.error(f"Unsupported step type: {step.type}")
        raise RuntimeError(f"Unsupported step type: {step.type}")

    def _execute_sql_step(self, step: Step):
        logging.debug(f"Reading query from file: {step.extract_source}")
        with open(step.extract_source, 'r', encoding='utf-8') as file:
            query = file.read()

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

    def _execute_python_step(self, step: Step):

        logging.debug(f"Executing Python script: {step.extract_source}")
        db_path = str(self.db_path_prefix / DB_NAME)
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

            self._run_python_script(venv_exec_cmd, step.extract_source, db_path, credential_config)

    @staticmethod
    def _install_dependencies(venv_exec_cmd, dependencies):
        logging.info(f"Installing dependencies: {', '.join(dependencies)}")
        try:
            logging.debug("Upgrading local pip")
            run(
                [
                    venv_exec_cmd,
                    "-m",
                    "pip",
                    "install",
                    "--upgrade",
                    "pip",
                    "--require-virtualenv",
                    "--quiet",
                    "--no-input",
                    "--disable-pip-version-check",
                ],
                check=True,
                stdout=DEVNULL,
                stderr=DEVNULL,
            )
            run(
                [
                    venv_exec_cmd,
                    "-m",
                    "pip",
                    "install",
                    *dependencies,
                    "--require-virtualenv",
                    "--quiet",
                    "--no-input",
                    "--disable-pip-version-check",
                ],
                check=True,
                stdout=DEVNULL,
                stderr=DEVNULL,
            )
        except CalledProcessError as e:
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
        db_path = str(self.db_path_prefix / DB_NAME)

        with duckdb.connect(db_path) as conn:
            # TODO: Add support for figuring out data types from SQLALCHEMY result object result.cursor.description is not reliable
            schema = ' STRING, '.join(result.columns) + ' STRING'

            # Handle write modes
            if mode == 'overwrite':
                conn.execute(f"CREATE OR REPLACE TABLE {step_name} ({schema})")
            elif mode == 'append' and step_name not in conn.get_table_names(""):
                conn.execute(f"CREATE TABLE {step_name} ({schema})")

            # Batch insert using prepared statements
            placeholders = ', '.join(['?' for _ in result.columns])
            insert_query = f"INSERT INTO {step_name} VALUES ({placeholders})"

            conn.executemany(insert_query, result.rows)

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
