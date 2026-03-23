import logging
from pathlib import Path
from collections.abc import Mapping

from databricks.labs.lakebridge.assessments.pipeline import PipelineClass
from databricks.labs.lakebridge.assessments.profiler_config import PipelineConfig, Step
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
        # TODO: Choose a better name for this.
        config = PipelineClass.load_config_from_yaml(config_file)
        new_steps = [step.copy(extract_source=str(path_prefix / step.extract_source)) for step in config.steps]
        return config.copy(steps=new_steps)

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

    @staticmethod
    def _configure_teradata_pipeline(
        pipeline_config: PipelineConfig, connect_config: Mapping[str, object]
    ) -> PipelineConfig:
        profiler_config = connect_config.get("profiler")
        use_pdcr = True
        if isinstance(profiler_config, Mapping):
            use_pdcr = bool(profiler_config.get("use_pdcr", True))

        if use_pdcr:
            return pipeline_config

        pdcr_step_names = {"td_pdcr_info_agg_extract", "td_pdcr_sp_exe_info_agg_extract"}
        updated_steps: list[Step] = []
        for step in pipeline_config.steps:
            if step.name in pdcr_step_names and step.type != "ddl":
                updated_steps.append(step.copy(flag="inactive"))
            elif step.name == "td_dbql_core_info_extract":
                updated_steps.append(step.copy(flag="active"))
            else:
                updated_steps.append(step)
        logger.info("Teradata profiler configured without PDCR; using DBQL core fallback extract.")
        return pipeline_config.copy(steps=updated_steps)

    @staticmethod
    def _is_pdcr_requested(connect_config: Mapping[str, object] | None) -> bool:
        if not connect_config:
            return True
        profiler_config = connect_config.get("profiler")
        if isinstance(profiler_config, Mapping):
            return bool(profiler_config.get("use_pdcr", True))
        return True

    @staticmethod
    def _has_pdcr_access(extractor: DatabaseManager) -> bool:
        # Lightweight probes: if these relations are inaccessible/missing, fallback to DBQL core.
        probes = (
            "SELECT TOP 1 1 AS pdcr_probe FROM PDCRINFO.DBQLogTbl_Hst",
            "SELECT TOP 1 1 AS pdcr_probe FROM PDCRINFO.UserInfo",
        )
        try:
            for query in probes:
                extractor.fetch(query)
            return True
        except Exception as e:  # noqa: BLE001 - fallback logic intentionally catches connector/SQL errors
            logger.warning(f"PDCR preflight check failed; using DBQL core fallback. Details: {e}")
            return False

    def _execute(self, platform: str, pipeline_config: PipelineConfig, extractor=None) -> None:
        try:
            connect_config = None
            if extractor is None:
                if CONNECTOR_REQUIRED[platform]:
                    cred_manager = create_credential_manager(PRODUCT_NAME, EnvGetter())
                    connect_config = cred_manager.get_credentials(platform)
                    extractor = DatabaseManager(platform, connect_config)
                else:
                    extractor = None

            if platform == "teradata" and connect_config is not None:
                pdcr_requested = self._is_pdcr_requested(connect_config)
                if pdcr_requested and extractor is not None and not self._has_pdcr_access(extractor):
                    # Autodetect runtime capability and fallback when PDCR tables are unavailable.
                    pipeline_config = self._configure_teradata_pipeline(
                        pipeline_config, {"profiler": {"use_pdcr": False}}
                    )
                else:
                    pipeline_config = self._configure_teradata_pipeline(pipeline_config, connect_config)

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
