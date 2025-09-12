import dataclasses
import logging
import os
import webbrowser
from collections.abc import Set, Callable, Sequence
from pathlib import Path
from typing import Any, cast

from databricks.labs.blueprint.installation import Installation, JsonValue, SerdeError
from databricks.labs.blueprint.installer import InstallState
from databricks.labs.blueprint.tui import Prompts
from databricks.labs.blueprint.wheels import ProductInfo
from databricks.sdk import WorkspaceClient
from databricks.sdk.errors import NotFound, PermissionDenied

from databricks.labs.lakebridge.__about__ import __version__
from databricks.labs.lakebridge.config import (
    DatabaseConfig,
    ReconcileConfig,
    LakebridgeConfiguration,
    ReconcileMetadataConfig,
    TranspileConfig,
)
from databricks.labs.lakebridge.contexts.application import ApplicationContext
from databricks.labs.lakebridge.deployment.configurator import ResourceConfigurator
from databricks.labs.lakebridge.deployment.installation import WorkspaceInstallation
from databricks.labs.lakebridge.reconcile.constants import ReconReportType, ReconSourceType
from databricks.labs.lakebridge.transpiler.installers import (
    BladebridgeInstaller,
    MorpheusInstaller,
    TranspilerInstaller,
)
from databricks.labs.lakebridge.transpiler.repository import TranspilerRepository

logger = logging.getLogger(__name__)

TRANSPILER_WAREHOUSE_PREFIX = "Lakebridge Transpiler Validation"


class WorkspaceInstaller:
    def __init__(
        self,
        ws: WorkspaceClient,
        prompts: Prompts,
        installation: Installation,
        install_state: InstallState,
        product_info: ProductInfo,
        resource_configurator: ResourceConfigurator,
        workspace_installation: WorkspaceInstallation,
        environ: dict[str, str] | None = None,
        *,
        is_interactive: bool = True,
        transpiler_repository: TranspilerRepository = TranspilerRepository.user_home(),
        transpiler_installers: Sequence[Callable[[TranspilerRepository], TranspilerInstaller]] = (
            BladebridgeInstaller,
            MorpheusInstaller,
        ),
    ):
        self._ws = ws
        self._prompts = prompts
        self._installation = installation
        self._install_state = install_state
        self._product_info = product_info
        self._resource_configurator = resource_configurator
        self._ws_installation = workspace_installation
        # TODO: Refactor the 'prompts' property in preference to using this flag, which should be redundant.
        self._is_interactive = is_interactive
        self._transpiler_repository = transpiler_repository
        self._transpiler_installer_factories = transpiler_installers

        if not environ:
            environ = dict(os.environ.items())

        if "DATABRICKS_RUNTIME_VERSION" in environ:
            msg = "WorkspaceInstaller is not supposed to be executed in Databricks Runtime"
            raise SystemExit(msg)

    @property
    def _transpiler_installers(self) -> Set[TranspilerInstaller]:
        return frozenset(factory(self._transpiler_repository) for factory in self._transpiler_installer_factories)

    def run(
        self,
        module: str,
        config: LakebridgeConfiguration | None = None,
        artifact: str | None = None,
    ) -> LakebridgeConfiguration:
        logger.debug(f"Initializing workspace installation for module: {module} (config: {config})")
        if module == "transpile" and artifact:
            self._install_artifact(artifact)
        elif module in {"transpile", "all"}:
            for transpiler_installer in self._transpiler_installers:
                transpiler_installer.install()
        if not config:
            config = self.configure(module)
        if self._is_testing():
            return config
        self._ws_installation.install(config)
        logger.info("Installation completed successfully! Please refer to the documentation for the next steps.")
        return config

    def upgrade_installed_transpilers(self) -> bool:
        """Detect and upgrade, if possible and necessary, installed transpilers."""
        installed_transpilers = self._transpiler_repository.all_transpiler_names()
        if installed_transpilers:
            logger.info(f"Detected installed transpilers: {sorted(installed_transpilers)}")
        upgraded = False
        for transpiler_installer in self._transpiler_installers:
            name = transpiler_installer.name
            if name in installed_transpilers:
                logger.info(f"Checking for {name} upgrades...")
                upgraded |= transpiler_installer.install()
        # If we upgraded anything, the configuration process needs to run again.
        if upgraded:
            config = self.configure("transpile")
            if not self._is_testing():
                self._ws_installation.install(config)
        return upgraded

    def _install_artifact(self, artifact: str) -> None:
        path = Path(artifact)
        if not path.exists():
            logger.error(f"Could not locate artifact {artifact}")
            return
        for transpiler_installer in self._transpiler_installers:
            if transpiler_installer.can_install(path):
                transpiler_installer.install(path)
                break
        else:
            logger.fatal(f"Cannot install unsupported artifact: {artifact}")

    def configure(self, module: str) -> LakebridgeConfiguration:
        match module:
            case "transpile":
                logger.info("Configuring lakebridge `transpile`.")
                return LakebridgeConfiguration(self._configure_transpile(), None)
            case "reconcile":
                logger.info("Configuring lakebridge `reconcile`.")
                return LakebridgeConfiguration(None, self._configure_reconcile())
            case "all":
                logger.info("Configuring lakebridge `transpile` and `reconcile`.")
                return LakebridgeConfiguration(
                    self._configure_transpile(),
                    self._configure_reconcile(),
                )
            case _:
                raise ValueError(f"Invalid input: {module}")

    def _is_testing(self):
        return self._product_info.product_name() != "lakebridge"

    def _configure_transpile(self) -> TranspileConfig | None:
        try:
            config = self._installation.load(TranspileConfig)
            logger.info("Lakebridge `transpile` is already installed on this workspace.")
            if not self._is_interactive:
                logger.debug("Installation is not interactive, keeping existing configuration.")
                return config
            if not self._prompts.confirm("Do you want to override the existing installation?"):
                return config
        except NotFound:
            logger.info("Couldn't find existing `transpile` installation")
        except (PermissionDenied, SerdeError, ValueError, AttributeError):
            install_dir = self._installation.install_folder()
            logger.warning(
                f"Existing `transpile` installation at {install_dir} is corrupted. Continuing new installation..."
            )

        if not self._is_interactive:
            logger.warning("Installation is not interactive, skipping configuration of transpilers.")
            return None

        config = self._configure_new_transpile_installation()
        logger.info("Finished configuring lakebridge `transpile`.")
        return config

    def _configure_new_transpile_installation(self) -> TranspileConfig:
        default_config = self._prompt_for_new_transpile_installation()
        runtime_config = None
        catalog_name = "remorph"
        schema_name = "transpiler"
        if not default_config.skip_validation:
            catalog_name = self._configure_catalog()
            schema_name = self._configure_schema(catalog_name, "transpile")
            self._has_necessary_access(catalog_name, schema_name)
            warehouse_id = self._resource_configurator.prompt_for_warehouse_setup(TRANSPILER_WAREHOUSE_PREFIX)
            runtime_config = {"warehouse_id": warehouse_id}

        config = dataclasses.replace(
            default_config,
            catalog_name=catalog_name,
            schema_name=schema_name,
            sdk_config=runtime_config,
        )
        self._save_config(config)
        return config

    def _all_installed_dialects(self) -> list[str]:
        return sorted(self._transpiler_repository.all_dialects())

    def _transpilers_with_dialect(self, dialect: str) -> list[str]:
        return sorted(self._transpiler_repository.transpilers_with_dialect(dialect))

    def _transpiler_config_path(self, transpiler: str) -> Path:
        return self._transpiler_repository.transpiler_config_path(transpiler)

    def _prompt_for_new_transpile_installation(self) -> TranspileConfig:
        install_later = "Set it later"
        # TODO tidy this up, logger might not display the below in console...
        logger.info("Please answer a few questions to configure lakebridge `transpile`")
        all_dialects = [install_later, *self._all_installed_dialects()]
        source_dialect: str | None = self._prompts.choice("Select the source dialect:", all_dialects, sort=False)
        if source_dialect == install_later:
            source_dialect = None
        transpiler_name: str | None = None
        transpiler_config_path: Path | None = None
        if source_dialect:
            transpilers = self._transpilers_with_dialect(source_dialect)
            if len(transpilers) > 1:
                transpilers = [install_later] + transpilers
                transpiler_name = self._prompts.choice("Select the transpiler:", transpilers, sort=False)
                if transpiler_name == install_later:
                    transpiler_name = None
            else:
                transpiler_name = next(t for t in transpilers)
                logger.info(f"Lakebridge will use the {transpiler_name} transpiler")
            if transpiler_name:
                transpiler_config_path = self._transpiler_config_path(transpiler_name)
        transpiler_options: dict[str, JsonValue] | None = None
        if transpiler_config_path:
            transpiler_options = self._prompt_for_transpiler_options(
                cast(str, transpiler_name), cast(str, source_dialect)
            )
        input_source: str | None = self._prompts.question(
            "Enter input SQL path (directory/file)", default=install_later
        )
        if input_source == install_later:
            input_source = None
        output_folder = self._prompts.question("Enter output directory", default="transpiled")
        # When defaults are passed along we need to use absolute paths to avoid issues with relative paths
        if output_folder == "transpiled":
            output_folder = str(Path.cwd() / "transpiled")
        error_file_path = self._prompts.question("Enter error file path", default="errors.log")
        if error_file_path == "errors.log":
            error_file_path = str(Path.cwd() / "errors.log")

        run_validation = self._prompts.confirm(
            "Would you like to validate the syntax and semantics of the transpiled queries?"
        )

        return TranspileConfig(
            transpiler_config_path=str(transpiler_config_path) if transpiler_config_path is not None else None,
            transpiler_options=transpiler_options,
            source_dialect=source_dialect,
            skip_validation=(not run_validation),
            input_source=input_source,
            output_folder=output_folder,
            error_file_path=error_file_path,
        )

    def _prompt_for_transpiler_options(self, transpiler_name: str, source_dialect: str) -> dict[str, Any] | None:
        config_options = self._transpiler_repository.transpiler_config_options(transpiler_name, source_dialect)
        if len(config_options) == 0:
            return None
        return {option.flag: option.prompt_for_value(self._prompts) for option in config_options}

    def _configure_catalog(self) -> str:
        return self._resource_configurator.prompt_for_catalog_setup()

    def _configure_schema(
        self,
        catalog: str,
        default_schema_name: str,
    ) -> str:
        return self._resource_configurator.prompt_for_schema_setup(
            catalog,
            default_schema_name,
        )

    def _configure_reconcile(self) -> ReconcileConfig:
        try:
            self._installation.load(ReconcileConfig)
            logger.info("Lakebridge `reconcile` is already installed on this workspace.")
            if not self._prompts.confirm("Do you want to override the existing installation?"):
                # TODO: Exit gracefully, without raising SystemExit
                raise SystemExit(
                    "Lakebridge `reconcile` is already installed and no override has been requested. Exiting..."
                )
        except NotFound:
            logger.info("Couldn't find existing `reconcile` installation")
        except (PermissionDenied, SerdeError, ValueError, AttributeError):
            install_dir = self._installation.install_folder()
            logger.warning(
                f"Existing `reconcile` installation at {install_dir} is corrupted. Continuing new installation..."
            )

        config = self._configure_new_reconcile_installation()
        logger.info("Finished configuring lakebridge `reconcile`.")
        return config

    def _configure_new_reconcile_installation(self) -> ReconcileConfig:
        default_config = self._prompt_for_new_reconcile_installation()
        self._save_config(default_config)
        return default_config

    def _prompt_for_new_reconcile_installation(self) -> ReconcileConfig:
        logger.info("Please answer a few questions to configure lakebridge `reconcile`")
        data_source = self._prompts.choice(
            "Select the Data Source:", [source_type.value for source_type in ReconSourceType]
        )
        report_type = self._prompts.choice(
            "Select the report type:", [report_type.value for report_type in ReconReportType]
        )
        scope_name = self._prompts.question(
            f"Enter Secret scope name to store `{data_source.capitalize()}` connection details / secrets",
            default=f"remorph_{data_source}",
        )

        db_config = self._prompt_for_reconcile_database_config(data_source)
        metadata_config = self._prompt_for_reconcile_metadata_config()

        return ReconcileConfig(
            data_source=data_source,
            report_type=report_type,
            secret_scope=scope_name,
            database_config=db_config,
            metadata_config=metadata_config,
        )

    def _prompt_for_reconcile_database_config(self, source) -> DatabaseConfig:
        source_catalog = None
        if source == ReconSourceType.SNOWFLAKE.value:
            source_catalog = self._prompts.question(f"Enter source catalog name for `{source.capitalize()}`")

        schema_prompt = f"Enter source schema name for `{source.capitalize()}`"
        if source in {ReconSourceType.ORACLE.value}:
            schema_prompt = f"Enter source database name for `{source.capitalize()}`"

        source_schema = self._prompts.question(schema_prompt)
        target_catalog = self._prompts.question("Enter target catalog name for Databricks")
        target_schema = self._prompts.question("Enter target schema name for Databricks")

        return DatabaseConfig(
            source_schema=source_schema,
            target_catalog=target_catalog,
            target_schema=target_schema,
            source_catalog=source_catalog,
        )

    def _prompt_for_reconcile_metadata_config(self) -> ReconcileMetadataConfig:
        logger.info("Configuring reconcile metadata.")
        catalog = self._configure_catalog()
        schema = self._configure_schema(
            catalog,
            "reconcile",
        )
        volume = self._configure_volume(catalog, schema, "reconcile_volume")
        self._has_necessary_access(catalog, schema, volume)
        return ReconcileMetadataConfig(catalog=catalog, schema=schema, volume=volume)

    def _configure_volume(
        self,
        catalog: str,
        schema: str,
        default_volume_name: str,
    ) -> str:
        return self._resource_configurator.prompt_for_volume_setup(
            catalog,
            schema,
            default_volume_name,
        )

    def _save_config(self, config: TranspileConfig | ReconcileConfig):
        logger.info(f"Saving configuration file {config.__file__}")
        self._installation.save(config)
        ws_file_url = self._installation.workspace_link(config.__file__)
        if self._prompts.confirm(f"Open config file {ws_file_url} in the browser?"):
            webbrowser.open(ws_file_url)

    def _has_necessary_access(self, catalog_name: str, schema_name: str, volume_name: str | None = None):
        self._resource_configurator.has_necessary_access(catalog_name, schema_name, volume_name)


def installer(
    ws: WorkspaceClient,
    transpiler_repository: TranspilerRepository,
    *,
    is_interactive: bool,
) -> WorkspaceInstaller:
    app_context = ApplicationContext(_verify_workspace_client(ws))
    return WorkspaceInstaller(
        app_context.workspace_client,
        app_context.prompts,
        app_context.installation,
        app_context.install_state,
        app_context.product_info,
        app_context.resource_configurator,
        app_context.workspace_installation,
        transpiler_repository=transpiler_repository,
        is_interactive=is_interactive,
    )


def _verify_workspace_client(ws: WorkspaceClient) -> WorkspaceClient:
    """Verifies the workspace client configuration, ensuring it has the correct product info."""

    # Using reflection to set right value for _product_info for telemetry
    product_info = getattr(ws.config, '_product_info')
    if product_info[0] != "lakebridge":
        setattr(ws.config, '_product_info', ('lakebridge', __version__))

    return ws
