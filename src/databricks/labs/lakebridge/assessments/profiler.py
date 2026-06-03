import logging
from pathlib import Path

from databricks.labs.lakebridge.assessments.pipeline import PipelineClass, make_profiler_db_filename
from databricks.labs.lakebridge.assessments.profiler_config import PipelineConfig
from databricks.labs.lakebridge.connections.database_manager import DatabaseManager
from databricks.labs.lakebridge.connections.credential_manager import (
    create_credential_manager,
)
from databricks.labs.lakebridge.connections.env_getter import EnvGetter
from databricks.labs.lakebridge.assessments import (
    PRODUCT_NAME,
    PRODUCT_PATH_PREFIX,
    PLATFORM_TO_SOURCE_TECHNOLOGY_CFG,
    CONNECTOR_REQUIRED,
)

logger = logging.getLogger(__name__)


def default_output_folder(platform: str) -> Path:
    return Path.home() / ".databricks" / "labs" / "lakebridge_profilers" / f"{platform}_assessment"


class Profiler:

    def __init__(self, platform: str, pipeline_configs: PipelineConfig | None = None):
        self._platform = platform
        self._pipeline_config = pipeline_configs

    @classmethod
    def create(cls, platform: str) -> "Profiler":
        pipeline_config_path = PLATFORM_TO_SOURCE_TECHNOLOGY_CFG[platform]
        pipeline_config_absolute_path = Profiler._locate_config(pipeline_config_path)
        pipeline_config = Profiler.path_modifier(config_file=pipeline_config_absolute_path)
        return cls(platform, pipeline_config)

    @staticmethod
    def path_modifier(*, config_file: str | Path, path_prefix: Path = PRODUCT_PATH_PREFIX) -> PipelineConfig:
        # TODO: Choose a better name for this.
        config = PipelineClass.load_config_from_yaml(config_file)
        new_steps = [step.copy(extract_source=str(path_prefix / step.extract_source)) for step in config.steps]
        return config.copy(steps=new_steps)

    def profile(
        self,
        *,
        extractor: DatabaseManager | None = None,
        pipeline_config: PipelineConfig | None = None,
        output_folder: Path | None = None,
    ) -> None:
        platform = self._platform.lower()
        if not pipeline_config:
            if not self._pipeline_config:
                raise ValueError(f"Cannot Proceed without a valid pipeline configuration for {platform}")
            pipeline_config = self._pipeline_config
        resolved_output_folder = output_folder or default_output_folder(platform)
        self._execute(platform, pipeline_config, resolved_output_folder, extractor)

    @staticmethod
    def _setup_extractor(platform: str) -> DatabaseManager | None:
        if not CONNECTOR_REQUIRED[platform]:
            return None
        cred_manager = create_credential_manager(PRODUCT_NAME, EnvGetter())
        connect_config = cred_manager.get_credentials(platform)
        return DatabaseManager(platform, connect_config)

    def _execute(self, platform: str, pipeline_config: PipelineConfig, output_folder: Path, extractor=None) -> None:
        try:
            if extractor is None:
                extractor = Profiler._setup_extractor(platform)

            db_path = output_folder / make_profiler_db_filename(platform)
            result = PipelineClass(pipeline_config, extractor, db_path).execute()
            logger.info(f"Profiler extract written to {db_path.expanduser()}")
            logger.info(f"Profile execution has completed successfully for {platform} for more info check: {result}.")
        except FileNotFoundError as e:
            logger.error(f"Configuration file not found for source {platform}: {e}")
            raise FileNotFoundError(f"Configuration file not found for source {platform}: {e}") from e
        except Exception as e:
            logger.error(f"Error executing pipeline for source {platform}: {e}")
            raise RuntimeError(f"Pipeline execution failed for source {platform} : {e}") from e

    @staticmethod
    def _locate_config(config_path: str | Path) -> Path:
        config_file = PRODUCT_PATH_PREFIX / config_path
        if not config_file.exists():
            raise FileNotFoundError(f"Configuration file not found: {config_file}")
        return config_file
