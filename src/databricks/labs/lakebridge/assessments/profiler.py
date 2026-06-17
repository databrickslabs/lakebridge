import logging
from pathlib import Path

from databricks.labs.lakebridge.assessments import CONNECTOR_REQUIRED, PRODUCT_PATH_PREFIX
from databricks.labs.lakebridge.assessments.pipeline import PipelineClass, make_profiler_db_filename
from databricks.labs.lakebridge.assessments.profiler_config import PipelineConfig
from databricks.labs.lakebridge.connections.database_manager import DatabaseManager
from databricks.labs.lakebridge.connections.credential_manager import (
    create_credential_manager,
    cred_file,
)
from databricks.labs.lakebridge.connections.env_getter import EnvGetter

logger = logging.getLogger(__name__)


def default_output_folder(platform: str) -> Path:
    return Path.home() / ".databricks" / "labs" / "lakebridge_profilers" / f"{platform}_assessment"


def get_pipeline(source_system: str, variant: str | None) -> Path:
    file = "pipeline_config.yml"
    base = PRODUCT_PATH_PREFIX / f"src/databricks/labs/lakebridge/resources/assessments/{source_system}"
    return base / variant / file if variant else base / file


class Profiler:

    def __init__(
        self,
        source_system: str,
        connector_required: bool,
        variant: str | None = None,
        pipeline_configs: PipelineConfig | None = None,
    ):
        self._source_system = source_system
        self._variant = variant
        self._pipeline_config = pipeline_configs
        self._connector_required = connector_required

    @classmethod
    def create(cls, source_system: str, variant: str | None) -> "Profiler":
        pipeline_config_path = get_pipeline(source_system, variant)
        if not pipeline_config_path.exists():
            raise FileNotFoundError(f"Configuration file not found: {pipeline_config_path}")

        pipeline_config = Profiler.path_modifier(config_file=pipeline_config_path)
        connector_required = CONNECTOR_REQUIRED[source_system]
        return cls(source_system, connector_required, variant, pipeline_config)

    @staticmethod
    def path_modifier(*, config_file: str | Path, path_prefix: Path = PRODUCT_PATH_PREFIX) -> PipelineConfig:
        # TODO: Choose a better name for this.
        config = PipelineClass.load_config_from_yaml(config_file)
        new_steps = [step.copy(extract_source=str(path_prefix / step.extract_source)) for step in config.steps]
        return config.copy(steps=new_steps)

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
            extractor = Profiler._setup_extractor(source_system, cred_file_path) if self._connector_required else None
            db_path = output_folder / make_profiler_db_filename(source_system)
            result = PipelineClass(pipeline_config, extractor, db_path, cred_file_path).execute()
            logger.info(f"Profiler extract written to {db_path.expanduser()}")
            logger.info(
                f"Profile execution has completed successfully for {source_system} for more info check: {result}."
            )
        except FileNotFoundError as e:
            logger.error(f"Configuration file not found for source {source_system}: {e}")
            raise FileNotFoundError(f"Configuration file not found for source {source_system}: {e}") from e
        except Exception as e:
            logger.error(f"Error executing pipeline for source {source_system}: {e}")
            raise RuntimeError(f"Pipeline execution failed for source {source_system} : {e}") from e

    @staticmethod
    def _setup_extractor(source_system: str, cred_file_path: Path | None = None) -> DatabaseManager | None:
        cred_manager = create_credential_manager(source_system, EnvGetter(), creds_path=cred_file_path)
        connect_config = cred_manager.get_credentials(source_system)
        return DatabaseManager(source_system, connect_config)
