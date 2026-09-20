import json
import logging
import platform
import re
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import pandas as pd

from databricks.labs.lakebridge import __version__ as lakebridge_version
from databricks.labs.lakebridge.assessments import (
    PRODUCT_PATH_PREFIX,
    PROFILER_RUN_METADATA_TABLE,
    PROFILER_SOURCE_SYSTEM,
)
from databricks.labs.lakebridge.assessments.pipeline import (
    PipelineClass,
    StepExecutionResult,
    StepExecutionStatus,
    make_profiler_db_filename,
)
from databricks.labs.lakebridge.assessments.profiler_config import PipelineConfig
from databricks.labs.lakebridge.assessments.run_metadata import (
    PROFILER_RUN_METADATA_SCHEMA,
    ProfilerRunMetadata,
    ProfilerRunStatus,
)
from databricks.labs.lakebridge.assessments.variants import resolve_variant
from databricks.labs.lakebridge.resources.assessments.common.duckdb_helpers import connect_to_profiler_db
from databricks.labs.lakebridge.connections.credential_manager import (
    create_credential_manager,
    cred_file,
)
from databricks.labs.lakebridge.connections.database_manager import DatabaseConnector, create_connector
from databricks.labs.lakebridge.connections.env_getter import EnvGetter
from databricks.labs.lakebridge.resources.assessments.common.duckdb_helpers import save_to_duckdb

logger = logging.getLogger(__name__)


def default_output_folder(source_system: str) -> Path:
    return Path.home() / ".databricks" / "labs" / "lakebridge_profilers" / f"{source_system}_assessment"


def get_pipeline(source_system: str, variant: str | None) -> Path:
    file = "pipeline_config.yml"
    base = PRODUCT_PATH_PREFIX / f"src/databricks/labs/lakebridge/resources/assessments/{source_system}"
    return base / variant / file if variant else base / file


class Profiler:

    def __init__(
        self,
        source_system: str,
        variant: str | None = None,
        pipeline_configs: PipelineConfig | None = None,
    ):
        self._source_system = self._normalize_source_system(source_system)
        self._variant = variant
        self._pipeline_config = pipeline_configs

    @property
    def source_system(self) -> str:
        return self._source_system

    @classmethod
    def create(cls, source_system: str, variant: str | None = None, cred_file_path: Path | None = None) -> "Profiler":
        resolved_variant = resolve_variant(source_system, variant, cred_file_path=cred_file_path)
        pipeline_config_path = get_pipeline(source_system, resolved_variant)
        if not pipeline_config_path.exists():
            raise FileNotFoundError(f"Configuration file not found: {pipeline_config_path}")

        pipeline_config = Profiler.path_modifier(config_file=pipeline_config_path)
        return cls(source_system, resolved_variant, pipeline_config)

    @staticmethod
    def path_modifier(*, config_file: str | Path, path_prefix: Path = PRODUCT_PATH_PREFIX) -> PipelineConfig:
        # TODO: Choose a better name for this.
        config = PipelineClass.load_config_from_yaml(config_file)
        new_steps = []
        for step in config.steps:
            changes: dict[str, str] = {"extract_source": str(path_prefix / step.extract_source)}
            if step.ddl_source is not None:
                changes["ddl_source"] = str(path_prefix / step.ddl_source)
            new_steps.append(step.copy(**changes))
        return config.copy(steps=new_steps)

    @staticmethod
    def _normalize_source_system(source_system: str) -> str:
        """Casefold the label so every extract records one spelling of a given platform.

        The CLI only offers the canonical names, but a caller constructing the Profiler
        directly can pass anything. An unknown name is a warning rather than an error:
        it makes the extract harder to consume, not impossible to produce.
        """
        normalized = source_system.casefold()
        if normalized not in PROFILER_SOURCE_SYSTEM:
            logger.warning(
                f"Unknown source system {source_system!r}; consumers of {PROFILER_RUN_METADATA_TABLE} "
                f"expect one of: {', '.join(PROFILER_SOURCE_SYSTEM)}."
            )
        return normalized

    @staticmethod
    def run_status(results: list[StepExecutionResult]) -> str:
        if any(r.status == StepExecutionStatus.ERROR for r in results):
            return ProfilerRunStatus.FAILED.value
        if any(r.status == StepExecutionStatus.ABSENT for r in results):
            return ProfilerRunStatus.COMPLETE_WITH_ABSENCES.value
        return ProfilerRunStatus.COMPLETE.value

    def write_run_metadata(
        self,
        db_path: Path,
        pipeline_config: PipelineConfig,
        results: list[StepExecutionResult],
    ) -> None:
        """Persist source-independent run metadata into the DuckDB extract.

        Written at the end of every run so the row can include step outcomes.
        Best-effort: a write failure is logged and does not mask step results.
        """
        try:
            metadata = ProfilerRunMetadata(
                source_system=self._source_system,
                variant=self._variant,
                pipeline_name=pipeline_config.name,
                pipeline_version=pipeline_config.version,
                lakebridge_version=lakebridge_version,
                python_version=platform.python_version(),
                operating_system=platform.platform(),
                status=self.run_status(results),
                results=json.dumps(
                    [
                        {
                            "step_name": r.step_name,
                            "status": r.status.value,
                            "error_message": r.error_message,
                        }
                        for r in results
                    ]
                ),
                generated_at=datetime.now(timezone.utc),
            )
            save_to_duckdb(
                pd.DataFrame([asdict(metadata)]),
                PROFILER_RUN_METADATA_TABLE,
                str(db_path),
                schema=PROFILER_RUN_METADATA_SCHEMA,
            )
            logger.info(f"Wrote {PROFILER_RUN_METADATA_TABLE}: {metadata}")
        except (OSError, TypeError, ValueError, duckdb.Error):
            logger.warning(f"Failed to write {PROFILER_RUN_METADATA_TABLE}", exc_info=True)

    def profile(
        self,
        *,
        pipeline_config: PipelineConfig | None = None,
        output_folder: Path | None = None,
        cred_file_path: Path | None = None,
    ) -> None:

        if not pipeline_config:
            if not self._pipeline_config:
                raise ValueError(f"Cannot Proceed without a valid pipeline configuration for {self._source_system}")
            pipeline_config = self._pipeline_config
        resolved_output_folder = output_folder or default_output_folder(self._source_system)
        resolved_creds_path = cred_file_path or cred_file()
        self._execute(self._source_system, pipeline_config, resolved_output_folder, resolved_creds_path)

    def _execute(
        self,
        source_system: str,
        pipeline_config: PipelineConfig,
        output_folder: Path,
        cred_file_path: Path,
    ) -> None:
        try:
            # A source connector is needed for any step that isn't a self-managing python step.
            connector_required = any(step.type != "python" for step in pipeline_config.steps if step.flag == "active")
            extractor = Profiler._setup_extractor(source_system, cred_file_path) if connector_required else None
            db_path = output_folder / make_profiler_db_filename(source_system)
            results = PipelineClass(
                pipeline_config,
                extractor,
                db_path,
                cred_file_path,
            ).execute()
        except FileNotFoundError as e:
            logger.error(f"Configuration file not found for source {source_system}: {e}")
            raise FileNotFoundError(f"Configuration file not found for source {source_system}: {e}") from e
        except Exception as e:
            logger.error(f"Error executing pipeline for source {source_system}: {e}")
            raise RuntimeError(f"Pipeline execution failed for source {source_system} : {e}") from e

        self.write_run_metadata(db_path, pipeline_config, results)
        self._write_metadata(self._source_metadata(source_system, cred_file_path), db_path)

        failed = [r for r in results if r.status == StepExecutionStatus.ERROR]
        if failed:
            raise RuntimeError(f"Pipeline failed for {source_system}: {', '.join(r.step_name for r in failed)}")

        logger.info(f"Profiler extract written to {db_path.expanduser()}")
        logger.info(f"Profile execution has completed successfully for {source_system} for more info check: {results}.")

    @staticmethod
    def _setup_extractor(source_system: str, cred_file_path: Path | None = None) -> DatabaseConnector | None:
        cred_manager = create_credential_manager(source_system, EnvGetter(), creds_path=cred_file_path)
        connect_config = cred_manager.get_credentials(source_system)
        return create_connector(source_system, connect_config)

    @staticmethod
    def _source_metadata(source_system: str, cred_file_path: Path | None) -> dict:
        """Return the generic ``metadata`` block a configurator wrote under the source in creds.

        Configurators may record source-specific context there (e.g. the Redshift configurator
        stores the AWS region parsed from the endpoint host). Returns ``{}`` when creds or the
        block are absent, so the caller no-ops.
        """
        try:
            cred_manager = create_credential_manager(source_system, EnvGetter(), creds_path=cred_file_path)
            connect_config = cred_manager.get_credentials(source_system)
        except Exception:  # pylint: disable=broad-except  # creds are optional (e.g. python-only pipelines)
            return {}
        metadata = connect_config.get("metadata") if isinstance(connect_config, dict) else None
        return metadata if isinstance(metadata, dict) else {}

    @staticmethod
    def _write_metadata(metadata: dict, db_path: Path) -> None:
        """Persist a source's ``metadata`` block into the profiler DB, one column per key.

        Makes the extract self-contained: e.g. Redshift stores ``region`` so a downstream consumer
        that lacks the connection config can still compute Serverless cost (rpu_hours x region
        price). Generic (any keys) and best-effort -- a failure must not fail the profile. Keys are
        restricted to identifier-safe names since they become column names.
        """
        safe = {k: v for k, v in metadata.items() if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", str(k))}
        if not safe:
            return
        cols = list(safe)
        col_defs = ", ".join(f'"{c}" VARCHAR' for c in cols)
        placeholders = ", ".join("?" for _ in cols)
        values = [None if safe[c] is None else str(safe[c]) for c in cols]
        try:
            with connect_to_profiler_db(str(db_path.expanduser())) as conn:
                conn.execute(f"CREATE OR REPLACE TABLE profiler_metadata ({col_defs})")
                conn.execute(f"INSERT INTO profiler_metadata VALUES ({placeholders})", values)
            logger.info("Stored profiler_metadata columns %s for self-contained downstream analysis", cols)
        except Exception as e:  # pylint: disable=broad-except
            logger.warning("Could not persist profiler metadata: %s", e)
