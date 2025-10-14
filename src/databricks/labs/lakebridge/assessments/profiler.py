import logging
from pathlib import Path

from databricks.labs.lakebridge.assessments.pipeline import PipelineClass
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


class Profiler:

    def __init__(self, platform: str, pipeline_configs: PipelineConfig | None = None):
        self._platform = platform
        self._pipeline_config = pipeline_configs

    @classmethod
    def create(cls, platform: str) -> "Profiler":
        pipeline_config_path = PLATFORM_TO_SOURCE_TECHNOLOGY_CFG.get(platform, None)
        pipeline_config = None
        if pipeline_config_path:
            pipeline_config_absolute_path = Profiler._locate_config(pipeline_config_path)
            pipeline_config = Profiler.path_modifier(config_file=pipeline_config_absolute_path)
        return cls(platform, pipeline_config)

    @classmethod
    def supported_platforms(cls) -> list[str]:
        return list(PLATFORM_TO_SOURCE_TECHNOLOGY_CFG.keys())

    @staticmethod
    def path_modifier(*, config_file: str | Path, path_prefix: Path = PRODUCT_PATH_PREFIX) -> PipelineConfig:
        # TODO: Make this work install during developer mode
        config = PipelineClass.load_config_from_yaml(config_file)
        for step in config.steps:
            step.extract_source = f"{path_prefix}/{step.extract_source}"
        return config

    def profile(
        self,
        *,
        extractor: DatabaseManager | None = None,
        pipeline_config: PipelineConfig | None = None,
    ) -> None:
        platform = self._platform.lower()
        if not pipeline_config:
            if not self._pipeline_config:
                raise ValueError(f"Cannot Proceed without a valid pipeline configuration for {platform}")
            pipeline_config = self._pipeline_config
        self._execute(platform, pipeline_config, extractor)

    @staticmethod
    def _setup_extractor(platform: str) -> DatabaseManager | None:
        if not CONNECTOR_REQUIRED[platform]:
            return None
        cred_manager = create_credential_manager(PRODUCT_NAME, EnvGetter())
        connect_config = cred_manager.get_credentials(platform)
        return DatabaseManager(platform, connect_config)

    def _execute(self, platform: str, pipeline_config: PipelineConfig, extractor=None) -> None:
        try:
            if extractor is None:
                extractor = Profiler._setup_extractor(platform)

            result = PipelineClass(pipeline_config, extractor).execute()
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
