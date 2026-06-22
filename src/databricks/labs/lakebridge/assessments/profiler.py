import logging
from collections.abc import Callable
from pathlib import Path

from databricks.labs.lakebridge.assessments import PRODUCT_PATH_PREFIX, SOURCE_SYSTEM_VARIANTS, AUTO
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


# Azure SQL Database is the only single-database SQL Server edition. On-prem SQL Server and Azure SQL
# Managed Instance host multiple databases per instance and use the multi-database profiler variant.
SQLSERVER_AZURE_SQL_DB_ENGINE_EDITION = 5


def resolve_mssql_variant(cred_file_path: Path | None) -> str:
    """Pick the SQL Server profiler variant from the configured database and the server edition.

    A configured database scopes profiling to just that database (``single_db``) on any edition. When the
    database is left blank the edition decides: Azure SQL Database falls back to the connected database
    (``single_db``); on-prem SQL Server and Azure SQL Managed Instance profile every database (``multi_db``).
    """
    cred_manager = create_credential_manager("mssql", EnvGetter(), creds_path=cred_file_path)
    connect_config = cred_manager.get_credentials("mssql")
    if connect_config.get("database"):
        # The user picked a specific database -> profile only that one, regardless of edition.
        return "single_db"
    with DatabaseManager("mssql", connect_config) as db_manager:
        # SERVERPROPERTY returns sql_variant, which pyodbc cannot fetch (ODBC type -16); CAST to int.
        result = db_manager.fetch("SELECT CAST(SERVERPROPERTY('EngineEdition') AS INT) AS engine_edition")
    engine_edition = int(result.rows[0][0])
    variant = "single_db" if engine_edition == SQLSERVER_AZURE_SQL_DB_ENGINE_EDITION else "multi_db"
    logger.info(f"Detected SQL Server EngineEdition={engine_edition}; using '{variant}' profiler variant")
    return variant


# Sources whose variant is auto-detected from a live connection (probe) rather than supplied explicitly.
VARIANT_RESOLVERS: dict[str, Callable[[Path | None], str]] = {
    "mssql": resolve_mssql_variant,
}


def resolve_variant(source_system: str, variant: str | None, *, cred_file_path: Path | None = None) -> str | None:
    """Resolve the effective pipeline variant for a source.

    The ``SOURCE_SYSTEM_VARIANTS`` entry is either a tuple of explicit choices (the CLI prompts for one of
    these) or the ``AUTO`` marker (the variant is probed from a live connection here). ``variant`` is an
    explicit choice, the ``AUTO`` sentinel, or ``None``. This never prompts -- prompting lives in the CLI.
    """
    spec = SOURCE_SYSTEM_VARIANTS.get(source_system)
    if spec is None:
        if variant:
            logger.warning(f"Ignoring variant '{variant}': source system '{source_system}' has no variants.")
        return None

    if AUTO in spec:
        if variant != AUTO:
            logger.warning(f"Ignoring variant '{variant}'. Auto-detecting for source system '{source_system}'.")
        resolver = VARIANT_RESOLVERS.get(source_system)
        if resolver is None:
            raise ValueError(f"Source '{source_system}' is marked auto-detect but has no registered resolver.")
        return resolver(cred_file_path)

    if not variant:
        raise ValueError(f"No variant selected for '{source_system}' (choices: {spec}); the CLI must prompt for one.")

    chosen = variant.lower()
    if chosen not in spec:
        raise ValueError(f"Invalid variant '{chosen}' for '{source_system}'. Valid variants: {spec}.")
    return chosen


class Profiler:

    def __init__(
        self,
        source_system: str,
        variant: str | None = None,
        pipeline_configs: PipelineConfig | None = None,
    ):
        self._source_system = source_system
        self._variant = variant
        self._pipeline_config = pipeline_configs

    @classmethod
    def create(cls, source_system: str, variant: str | None = AUTO, cred_file_path: Path | None = None) -> "Profiler":
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
            # A source connector is needed for any step that isn't a self-managing python step.
            connector_required = any(step.type != "python" for step in pipeline_config.steps if step.flag == "active")
            extractor = Profiler._setup_extractor(source_system, cred_file_path) if connector_required else None
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
