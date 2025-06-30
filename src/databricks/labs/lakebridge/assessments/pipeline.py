from pathlib import Path
from subprocess import run, CalledProcessError, Popen, PIPE, STDOUT
from dataclasses import dataclass
from enum import Enum

import venv
import tempfile
import json
import logging
import yaml
import duckdb

from databricks.labs.lakebridge.connections.credential_manager import cred_file

from databricks.labs.lakebridge.assessments.profiler_config import PipelineConfig, Step
from databricks.labs.lakebridge.connections.database_manager import DatabaseManager

logger = logging.getLogger(__name__)
logger.setLevel("INFO")

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

    def execute(self) -> list[StepExecutionResult]:
        logging.info(f"Pipeline initialized with config: {self.config.name}, version: {self.config.version}")
        execution_results: list[StepExecutionResult] = []
        for step in self.config.steps:
            result = self._process_step(step)
            execution_results.append(result)
            logging.info(f"Step '{step.name}' completed with status: {result.status}")

        logging.info("Pipeline execution completed")
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
            result = self.executor.execute_query(query)

            # Save the result to duckdb
            self._save_to_db(result, step.name, str(step.mode))
        except Exception as e:
            logging.error(f"SQL execution failed: {str(e)}")
            raise RuntimeError(f"SQL execution failed: {str(e)}") from e

    def _execute_python_step(self, step: Step):

        logging.debug(f"Executing Python script: {step.extract_source}")
        db_path = str(self.db_path_prefix / DB_NAME)
        credential_config = str(cred_file("lakebridge"))

        # Create a temporary directory for the virtual environment
        with tempfile.TemporaryDirectory() as temp_dir:
            venv_dir = Path(temp_dir) / "venv"
            venv.create(venv_dir, with_pip=True)
            venv_python = venv_dir / "bin" / "python"
            venv_pip = venv_dir / "bin" / "pip"

            logger.info(f"Creating a virtual environment for Python script execution: ${venv_dir}")
            if step.dependencies:
                self._install_dependencies(venv_pip, step.dependencies)

            self._run_python_script(venv_python, step.extract_source, db_path, credential_config)

    @staticmethod
    def _install_dependencies(venv_pip, dependencies):
        logging.info(f"Installing dependencies: {', '.join(dependencies)}")
        try:
            logging.debug("Upgrading local pip")
            run([str(venv_pip), "install", "--upgrade", "pip"], check=True)
            run([str(venv_pip), "install", *dependencies], check=True)
        except CalledProcessError as e:
            logging.error(f"Failed to install dependencies: {e.stderr}")
            raise RuntimeError(f"Failed to install dependencies: {e.stderr}") from e

    @staticmethod
    def _run_python_script(venv_python, script_path, db_path, credential_config):
        output_lines = []
        try:
            with Popen(
                [
                    str(venv_python),
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
                if output.get("status") == "success":
                    logging.info(f"Python script completed: {output['message']}")
                else:
                    raise RuntimeError(f"Script reported error: {output.get('message', 'Unknown error')}")
            except json.JSONDecodeError:
                logging.info("Could not parse script output as JSON.")
        if process.returncode != 0:
            raise RuntimeError(f"Script execution failed with exit code {process.returncode}")

    def _save_to_db(self, result, step_name: str, mode: str, batch_size: int = 1000):
        self._create_dir(self.db_path_prefix)
        db_path = str(self.db_path_prefix / DB_NAME)

        with duckdb.connect(db_path) as conn:
            columns = result.keys()
            # TODO: Add support for figuring out data types from SQLALCHEMY result object result.cursor.description is not reliable
            schema = ' STRING, '.join(columns) + ' STRING'

            # Handle write modes
            if mode == 'overwrite':
                conn.execute(f"CREATE OR REPLACE TABLE {step_name} ({schema})")
            elif mode == 'append' and step_name not in conn.get_table_names(""):
                conn.execute(f"CREATE TABLE {step_name} ({schema})")

            # Batch insert using prepared statements
            placeholders = ', '.join(['?' for _ in columns])
            insert_query = f"INSERT INTO {step_name} VALUES ({placeholders})"

            # Fetch and insert rows in batches
            while True:
                rows = result.fetchmany(batch_size)
                if not rows:
                    break
                conn.executemany(insert_query, rows)

    @staticmethod
    def _create_dir(dir_path: Path):
        if not Path(dir_path).exists():
            dir_path.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def load_config_from_yaml(file_path: str) -> PipelineConfig:
        with open(file_path, 'r', encoding='utf-8') as file:
            data = yaml.safe_load(file)
        steps = [Step(**step) for step in data['steps']]
        return PipelineConfig(
            name=data['name'], version=data['version'], extract_folder=data['extract_folder'], steps=steps
        )
