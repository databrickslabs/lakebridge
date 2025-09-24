import asyncio
import dataclasses
import itertools
import json
import logging
import os
import re
import sys
import time
from collections.abc import Mapping
from pathlib import Path
from typing import NoReturn, TextIO

from databricks.sdk.service.sql import CreateWarehouseRequestWarehouseType
from databricks.sdk import WorkspaceClient

from databricks.labs.blueprint.cli import App
from databricks.labs.blueprint.entrypoint import get_logger, is_in_debug
from databricks.labs.blueprint.installation import RootJsonValue
from databricks.labs.blueprint.tui import Prompts


from databricks.labs.lakebridge.assessments.configure_assessment import (
    create_assessment_configurator,
    PROFILER_SOURCE_SYSTEM,
)

from databricks.labs.lakebridge.config import TranspileConfig
from databricks.labs.lakebridge.contexts.application import ApplicationContext
from databricks.labs.lakebridge.helpers.recon_config_utils import ReconConfigPrompts
from databricks.labs.lakebridge.helpers.telemetry_utils import make_alphanum_or_semver
from databricks.labs.lakebridge.install import installer
from databricks.labs.lakebridge.reconcile.runner import ReconcileRunner
from databricks.labs.lakebridge.lineage import lineage_generator
from databricks.labs.lakebridge.reconcile.recon_config import RECONCILE_OPERATION_NAME, AGG_RECONCILE_OPERATION_NAME
from databricks.labs.lakebridge.transpiler.execute import transpile as do_transpile
from databricks.labs.lakebridge.transpiler.lsp.lsp_engine import LSPEngine
from databricks.labs.lakebridge.transpiler.repository import TranspilerRepository
from databricks.labs.lakebridge.transpiler.sqlglot.sqlglot_engine import SqlglotEngine
from databricks.labs.lakebridge.transpiler.transpile_engine import TranspileEngine

from databricks.labs.lakebridge.transpiler.transpile_status import ErrorSeverity

lakebridge = App(__file__)
logger = get_logger(__file__)


def raise_validation_exception(msg: str) -> NoReturn:
    raise ValueError(msg)


def _create_warehouse(ws: WorkspaceClient) -> str:

    dbsql = ws.warehouses.create_and_wait(
        name=f"lakebridge-warehouse-{time.time_ns()}",
        warehouse_type=CreateWarehouseRequestWarehouseType.PRO,
        cluster_size="Small",  # Adjust size as needed
        auto_stop_mins=30,  # Auto-stop after 30 minutes of inactivity
        enable_serverless_compute=True,
        max_num_clusters=1,
    )

    if dbsql.id is None:
        raise RuntimeError(f"Failed to create warehouse {dbsql.name}")

    logger.info(f"Created warehouse with id: {dbsql.id}")
    return dbsql.id


def _remove_warehouse(ws: WorkspaceClient, warehouse_id: str):
    ws.warehouses.delete(warehouse_id)
    logger.info(f"Removed warehouse post installation with id: {warehouse_id}")


@lakebridge.command
def transpile(
    *,
    w: WorkspaceClient,
    transpiler_config_path: str | None = None,
    source_dialect: str | None = None,
    input_source: str | None = None,
    output_folder: str | None = None,
    error_file_path: str | None = None,
    skip_validation: str | None = None,
    catalog_name: str | None = None,
    schema_name: str | None = None,
    transpiler_repository: TranspilerRepository = TranspilerRepository.user_home(),
):
    """Transpiles source dialect to databricks dialect"""
    ctx = ApplicationContext(w)
    logger.debug(f"Preconfigured transpiler config: {ctx.transpile_config!r}")
    ctx.add_user_agent_extra("cmd", "execute-transpile")
    checker = _TranspileConfigChecker(ctx.transpile_config, ctx.prompts, transpiler_repository)
    checker.use_transpiler_config_path(transpiler_config_path)
    checker.use_source_dialect(source_dialect)
    checker.use_input_source(input_source)
    checker.use_output_folder(output_folder)
    checker.use_error_file_path(error_file_path)
    checker.use_skip_validation(skip_validation)
    checker.use_catalog_name(catalog_name)
    checker.use_schema_name(schema_name)
    config, engine = checker.check()
    logger.debug(f"Final configuration for transpilation: {config!r}")

    assert config.source_dialect is not None, "Source dialect has been validated by this point."
    ctx.add_user_agent_extra("transpiler_source_tech", make_alphanum_or_semver(config.source_dialect))
    plugin_name = engine.transpiler_name
    plugin_name = re.sub(r"\s+", "_", plugin_name)
    ctx.add_user_agent_extra("transpiler_plugin_name", plugin_name)
    user = ctx.current_user
    logger.debug(f"User: {user}")

    result = asyncio.run(_transpile(ctx, config, engine))
    # DO NOT Modify this print statement, it is used by the CLI to display results in GO Table Template
    print(json.dumps(result))


class _TranspileConfigChecker:
    """Helper class for the 'transpile' command to check and consolidate the configuration."""

    #
    # Configuration parameters can come from 3 sources:
    #  - Command-line arguments (e.g., --input-source, --output-folder, etc.)
    #  - The configuration file, stored in the user's workspace home directory.
    #  - User prompts.
    #
    # The conventions are:
    #  - Command-line arguments take precedence over the configuration file.
    #  - Prompting is a last resort, only used when a required configuration value has not been provided and does not
    #    have a default value.
    #  - An invalid value results in a halt, with the error message indicating the source of the invalid value. We do
    #    NOT attempt to recover from invalid values by looking for another source:
    #     - Prompting unexpectedly will break scripting and automation.
    #     - Using an alternate value will need to confusion because the behaviour will not be what the user expects.
    #
    # This ensures that we distinguish between:
    #  - Invalid command-line arguments:
    #    Resolution: fix the command-line argument value.
    #  - Invalid prompt responses:
    #    Resolution: provide a valid response to the prompt.
    #  - Invalid configuration file values:
    #    Resolution: fix the configuration file value, or provide the command-line argument to override it.
    #
    # Implementation details:
    #  - For command-line arguments and prompted values, we:
    #     - Log the raw values (prior to validation) at DEBUG level, using the repr() rendering.
    #     - Validate the values immediately, with the error message on failure mentioning the source of the value.
    #     - Only update the configuration if the validation passes.
    #  - Prompting only occurs when a value is required, but not provided via the command-line argument or the
    #    configuration file.
    #  - In addition to the above, a final validation of everything is required: this ensures that values from the
    #    configuration file are validated, and if we have a failure we know that's the source because other sources
    #    were already checked.
    #  - The interplay between the source dialect and the transpiler config path is handled with care:
    #      - The source dialect, needs to be consistent with the engine that transpiler config path, refers to.
    #      - The source dialect can be used to infer the transpiler config path.
    #
    # TODO: Refactor this class to eliminate a lof of the boilerplate and handle this more elegantly.

    _config: TranspileConfig
    """The workspace configuration for transpiling, updated from command-line arguments."""
    _prompts: Prompts
    """Prompting system, for requesting configuration that hasn't been provided."""
    _source_dialect_override: str | None = None
    """The source dialect provided on the command-line, if any."""
    _transpiler_repository: TranspilerRepository
    """The repository where available transpilers are installed."""

    def __init__(
        self,
        config: TranspileConfig | None,
        prompts: Prompts,
        transpiler_repository: TranspilerRepository,
    ) -> None:
        if config is None:
            logger.warning(
                "No workspace transpile configuration, use 'install-transpile' to (re)install and configure; using defaults for now."
            )
            config = TranspileConfig()
        self._config = config
        self._prompts = prompts
        self._transpiler_repository = transpiler_repository
        self._source_dialect_override = None

    @staticmethod
    def _validate_transpiler_config_path(transpiler_config_path: str, msg: str) -> None:
        """Validate the transpiler config path: it must be a valid path that exists."""
        # Note: the content is not validated here, but during loading of the engine.
        if not Path(transpiler_config_path).exists():
            raise_validation_exception(msg)

    def use_transpiler_config_path(self, transpiler_config_path: str | None) -> None:
        if transpiler_config_path is not None:
            logger.debug(f"Setting transpiler_config_path to: {transpiler_config_path!r}")
            self._validate_transpiler_config_path(
                transpiler_config_path,
                f"Invalid path for '--transpiler-config-path', does not exist: {transpiler_config_path}",
            )
            self._config = dataclasses.replace(self._config, transpiler_config_path=transpiler_config_path)

    def use_source_dialect(self, source_dialect: str | None) -> None:
        if source_dialect is not None:
            # Defer validation: depends on the transpiler config path, we'll deal with this later.
            logger.debug(f"Pending source_dialect override: {source_dialect!r}")
            self._source_dialect_override = source_dialect

    @staticmethod
    def _validate_input_source(input_source: str, msg: str) -> None:
        """Validate the input source: it must be a path that exists."""
        if not Path(input_source).exists():
            raise_validation_exception(msg)

    def use_input_source(self, input_source: str | None) -> None:
        if input_source is not None:
            logger.debug(f"Setting input_source to: {input_source!r}")
            self._validate_input_source(
                input_source, f"Invalid path for '--input-source', does not exist: {input_source}"
            )
            self._config = dataclasses.replace(self._config, input_source=input_source)

    def _prompt_input_source(self) -> None:
        prompted_input_source = self._prompts.question("Enter input SQL path (directory/file)").strip()
        logger.debug(f"Setting input_source to: {prompted_input_source!r}")
        self._validate_input_source(
            prompted_input_source, f"Invalid input source, path does not exist: {prompted_input_source}"
        )
        self._config = dataclasses.replace(self._config, input_source=prompted_input_source)

    def _check_input_source(self) -> None:
        config_input_source = self._config.input_source
        if config_input_source is None:
            self._prompt_input_source()
        else:
            self._validate_input_source(
                config_input_source, f"Invalid input source path configured, does not exist: {config_input_source}"
            )

    @staticmethod
    def _validate_output_folder(output_folder: str, msg: str) -> None:
        """Validate the output folder: it doesn't have to exist, but its parent must."""
        if not Path(output_folder).parent.exists():
            raise_validation_exception(msg)

    def use_output_folder(self, output_folder: str | None) -> None:
        if output_folder is not None:
            logger.debug(f"Setting output_folder to: {output_folder!r}")
            self._validate_output_folder(
                output_folder, f"Invalid path for '--output-folder', parent does not exist for: {output_folder}"
            )
            self._config = dataclasses.replace(self._config, output_folder=output_folder)

    def _prompt_output_folder(self) -> None:
        prompted_output_folder = self._prompts.question("Enter output folder path (directory)").strip()
        logger.debug(f"Setting output_folder to: {prompted_output_folder!r}")
        self._validate_output_folder(
            prompted_output_folder, f"Invalid output folder path, parent does not exist for: {prompted_output_folder}"
        )
        self._config = dataclasses.replace(self._config, output_folder=prompted_output_folder)

    def _check_output_folder(self) -> None:
        config_output_folder = self._config.output_folder
        if config_output_folder is None:
            self._prompt_output_folder()
        else:
            self._validate_output_folder(
                config_output_folder,
                f"Invalid output folder configured, parent does not exist for: {config_output_folder}",
            )

    @staticmethod
    def _validate_error_file_path(error_file_path: str | None, msg: str) -> None:
        """Value the error file path: it doesn't have to exist, but its parent must."""
        if error_file_path is not None and not Path(error_file_path).parent.exists():
            raise_validation_exception(msg)

    def use_error_file_path(self, error_file_path: str | None) -> None:
        if error_file_path is not None:
            logger.debug(f"Setting error_file_path to: {error_file_path!r}")
            self._validate_error_file_path(
                error_file_path, f"Invalid path for '--error-file-path', parent does not exist: {error_file_path}"
            )
            self._config = dataclasses.replace(self._config, error_file_path=error_file_path)

    def _check_error_file_path(self) -> None:
        config_error_file_path = self._config.error_file_path
        self._validate_error_file_path(
            config_error_file_path,
            f"Invalid error file path configured, parent does not exist for: {config_error_file_path}",
        )

    def use_skip_validation(self, skip_validation: str | None) -> None:
        if skip_validation is not None:
            skip_validation_lower = skip_validation.lower()
            if skip_validation_lower not in {"true", "false"}:
                msg = f"Invalid value for '--skip-validation': {skip_validation!r} must be 'true' or 'false'."
                raise_validation_exception(msg)
            new_skip_validation = skip_validation_lower == "true"
            logger.debug(f"Setting skip_validation to: {new_skip_validation!r}")
            self._config = dataclasses.replace(self._config, skip_validation=new_skip_validation)

    def use_catalog_name(self, catalog_name: str | None) -> None:
        if catalog_name:
            logger.debug(f"Setting catalog_name to: {catalog_name!r}")
            self._config = dataclasses.replace(self._config, catalog_name=catalog_name)

    def use_schema_name(self, schema_name: str | None) -> None:
        if schema_name:
            logger.debug(f"Setting schema_name to: {schema_name!r}")
            self._config = dataclasses.replace(self._config, schema_name=schema_name)

    def _configure_transpiler_config_path(self, source_dialect: str) -> TranspileEngine | None:
        """Configure the transpiler config path based on the requested source dialect."""
        # Names of compatible transpiler engines for the given dialect.
        compatible_transpilers = self._transpiler_repository.transpilers_with_dialect(source_dialect)
        match len(compatible_transpilers):
            case 0:
                # Nothing found for the specified dialect, fail.
                return None
            case 1:
                # Only one transpiler available for the specified dialect, use it.
                transpiler_name = next(iter(compatible_transpilers))
                logger.debug(f"Using only transpiler available for dialect {source_dialect!r}: {transpiler_name!r}")
            case _:
                # Multiple transpilers available for the specified dialect, prompt for which to use.
                logger.debug(
                    f"Multiple transpilers available for dialect {source_dialect!r}: {compatible_transpilers!r}"
                )
                transpiler_name = self._prompts.choice("Select the transpiler:", list(compatible_transpilers))
        transpiler_config_path = self._transpiler_repository.transpiler_config_path(transpiler_name)
        logger.info(f"Lakebridge will use the {transpiler_name} transpiler.")
        self._config = dataclasses.replace(self._config, transpiler_config_path=str(transpiler_config_path))
        return LSPEngine.from_config_path(transpiler_config_path)

    def _configure_source_dialect(
        self, source_dialect: str, engine: TranspileEngine | None, msg_prefix: str
    ) -> TranspileEngine:
        """Configure the source dialect, if possible, and return the transpiler engine."""
        if engine is None:
            engine = self._configure_transpiler_config_path(source_dialect)
            if engine is None:
                supported_dialects = ", ".join(self._transpiler_repository.all_dialects())
                msg = f"{msg_prefix}: {source_dialect!r} (supported dialects: {supported_dialects})"
                raise_validation_exception(msg)
            else:
                self._config = dataclasses.replace(self._config, source_dialect=source_dialect)
        else:
            # Check the source dialect against the engine.
            if source_dialect not in engine.supported_dialects:
                supported_dialects_description = ", ".join(engine.supported_dialects)
                msg = f"Invalid value for '--source-dialect': {source_dialect!r} must be one of: {supported_dialects_description}"
                raise_validation_exception(msg)
            self._config = dataclasses.replace(self._config, source_dialect=source_dialect)
        return engine

    def _prompt_source_dialect(self) -> TranspileEngine:
        # This is similar to the post-install prompting for the source dialect.
        supported_dialects = self._transpiler_repository.all_dialects()
        match len(supported_dialects):
            case 0:
                msg = "No transpilers are available, install using 'install-transpile' or use --transpiler-conf-path'."
                raise_validation_exception(msg)
            case 1:
                # Only one dialect available, use it.
                source_dialect = next(iter(supported_dialects))
                logger.debug(f"Using only source dialect available: {source_dialect!r}")
            case _:
                # Multiple dialects available, prompt for which to use.
                logger.debug(f"Multiple source dialects available, choice required: {supported_dialects!r}")
                source_dialect = self._prompts.choice("Select the source dialect:", list(supported_dialects))
        engine = self._configure_transpiler_config_path(source_dialect)
        assert engine is not None, "No transpiler engine available for a supported dialect; configuration is invalid."
        self._config = dataclasses.replace(self._config, source_dialect=source_dialect)
        return engine

    def _check_lsp_engine(self) -> TranspileEngine:
        #
        # This is somewhat complicated:
        #  - If there is no transpiler config path, we need to try to infer it from the source dialect.
        #  - If there is no source dialect, we need to prompt for it: but that depends on the transpiler config path.
        #
        # With this in mind, the steps here are:
        # 1. If the transpiler config path is set, check it exists and load the engine.
        # 2. If the source dialect is set,
        #      - If the transpiler config path is set: validate the source dialect against the engine.
        #      - If the transpiler config path is not set: search for a transpiler that satisfies the dialect:
        #          * If one is found, we're good to go.
        #          * If more than one is found, prompt for the transpiler config path.
        #          * If none are found, fail: no transpilers available for the specified dialect.
        #    At this point we have either halted, or we have a valid transpiler path and source dialect.
        # 3. If the source dialect is not set, we need to:
        #      a) Load the set of available dialects: just for the engine if transpiler config path is set, or for all
        #         available transpilers if not.
        #      b) Depending on the available dialects:
        #          - If there is only one dialect available, set it as the source dialect.
        #          - If there are multiple dialects available, prompt for which to use.
        #          - If there are no dialects available, fail: no transpilers available.
        #    At this point we have either halted, or we have a valid transpiler path and source dialect.
        #
        # TODO: Deal with the transpiler options, and filtering them for the engine.
        #

        # Step 1: Check the transpiler config path.
        engine: TranspileEngine | None
        transpiler_config_path = self._config.transpiler_config_path
        if transpiler_config_path is not None:
            self._validate_transpiler_config_path(
                transpiler_config_path,
                f"Error: Invalid value for '--transpiler-config-path': '{str(transpiler_config_path)}', file does not exist.",
            )
            path = Path(transpiler_config_path)
            engine = LSPEngine.from_config_path(path)
        else:
            engine = None
        del transpiler_config_path

        # Step 2: Check the source dialect, assuming it has been specified, and infer the transpiler config path if necessary.
        source_dialect = self._source_dialect_override
        if source_dialect is not None:
            logger.debug(f"Setting source_dialect override: {source_dialect!r}")
            engine = self._configure_source_dialect(source_dialect, engine, "Invalid value for '--source-dialect'")
        else:
            source_dialect = self._config.source_dialect
            if source_dialect is not None:
                logger.debug(f"Using configured source_dialect: {source_dialect!r}")
                engine = self._configure_source_dialect(source_dialect, engine, "Invalid configured source dialect")
            else:
                # Step 3: Source dialect is not set, we need to prompt for it.
                logger.debug("No source_dialect available, prompting.")
                engine = self._prompt_source_dialect()
        return engine

    def _check_transpiler_options(self, engine: TranspileEngine) -> None:
        if not isinstance(engine, LSPEngine):
            return
        assert self._config.source_dialect is not None, "Source dialect must be set before checking transpiler options."
        options_for_dialect = engine.options_for_dialect(self._config.source_dialect)
        transpiler_options = self._config.transpiler_options
        if not isinstance(transpiler_options, Mapping):
            return
        checked_options = {
            option.flag: (
                transpiler_options[option.flag]
                if option.flag in transpiler_options
                else option.prompt_for_value(self._prompts)
            )
            for option in options_for_dialect
        }
        self._config = dataclasses.replace(self._config, transpiler_options=checked_options)

    def check(self) -> tuple[TranspileConfig, TranspileEngine]:
        """Checks that all configuration parameters are present and valid."""
        logger.debug(f"Checking config: {self._config!r}")

        self._check_input_source()
        self._check_output_folder()
        self._check_error_file_path()
        # No validation here required for:
        #   - skip_validation: it is a boolean flag, mandatory, and has a default: so no further validation is needed.
        #   - catalog_name and schema_name: they are mandatory, but have a default.
        # TODO: if validation is enabled, we should check that the catalog and schema names are valid.

        # This covers: transpiler_config_path, source_dialect
        engine = self._check_lsp_engine()

        # Last thing: the configuration may have transpiler-specific options, check them.
        self._check_transpiler_options(engine)

        config = self._config
        logger.debug(f"Validated config: {config!r}")
        return config, engine


async def _transpile(ctx: ApplicationContext, config: TranspileConfig, engine: TranspileEngine) -> RootJsonValue:
    """Transpiles source dialect to databricks dialect"""
    ctx.add_user_agent_extra("cmd", "execute-transpile")
    user = ctx.current_user
    logger.debug(f"User: {user}")
    _override_workspace_client_config(ctx, config.sdk_config)
    status, errors = await do_transpile(ctx.workspace_client, engine, config)

    logger.debug(f"Transpilation completed with status: {status}")

    for path, errors_by_path in itertools.groupby(errors, key=lambda x: x.path):
        errs = list(errors_by_path)
        errors_by_severity = {
            severity.name: len(list(errors)) for severity, errors in itertools.groupby(errs, key=lambda x: x.severity)
        }
        reports = []
        reported_severities = [ErrorSeverity.ERROR, ErrorSeverity.WARNING]
        for severity in reported_severities:
            if severity.name in errors_by_severity:
                word = str.lower(severity.name) + "s" if errors_by_severity[severity.name] > 1 else ""
                reports.append(f"{errors_by_severity[severity.name]} {word}")

        msg = ", ".join(reports) + " found"

        if ErrorSeverity.ERROR.name in errors_by_severity:
            logger.error(f"{path}: {msg}")
        elif ErrorSeverity.WARNING.name in errors_by_severity:
            logger.warning(f"{path}: {msg}")

    # Table Template in labs.yml requires the status to be list of dicts Do not change this
    return [status]


def _override_workspace_client_config(ctx: ApplicationContext, overrides: dict[str, str] | None) -> None:
    """
    Override the Workspace client's SDK config with the user provided SDK config.
    Users can provide the cluster_id and warehouse_id during the installation.
    This will update the default config object in-place.
    """
    if not overrides:
        return

    warehouse_id = overrides.get("warehouse_id")
    if warehouse_id:
        ctx.connect_config.warehouse_id = warehouse_id

    cluster_id = overrides.get("cluster_id")
    if cluster_id:
        ctx.connect_config.cluster_id = cluster_id


@lakebridge.command
def reconcile(*, w: WorkspaceClient) -> None:
    """[EXPERIMENTAL] Reconciles source to Databricks datasets"""
    ctx = ApplicationContext(w)
    ctx.add_user_agent_extra("cmd", "execute-reconcile")
    user = ctx.current_user
    logger.debug(f"User: {user}")
    recon_runner = ReconcileRunner(
        ctx.workspace_client,
        ctx.installation,
        ctx.install_state,
        ctx.prompts,
    )
    recon_runner.run(operation_name=RECONCILE_OPERATION_NAME)


@lakebridge.command
def aggregates_reconcile(*, w: WorkspaceClient) -> None:
    """[EXPERIMENTAL] Reconciles Aggregated source to Databricks datasets"""
    ctx = ApplicationContext(w)
    ctx.add_user_agent_extra("cmd", "execute-aggregates-reconcile")
    user = ctx.current_user
    logger.debug(f"User: {user}")
    recon_runner = ReconcileRunner(
        ctx.workspace_client,
        ctx.installation,
        ctx.install_state,
        ctx.prompts,
    )

    recon_runner.run(operation_name=AGG_RECONCILE_OPERATION_NAME)


@lakebridge.command
def generate_lineage(
    *,
    w: WorkspaceClient,
    source_dialect: str | None = None,
    input_source: str,
    output_folder: str,
) -> None:
    """[Experimental] Generates a lineage of source SQL files or folder"""
    ctx = ApplicationContext(w)
    logger.debug(f"User: {ctx.current_user}")
    if not os.path.exists(input_source):
        raise_validation_exception(f"Invalid path for '--input-source': Path '{input_source}' does not exist.")
    if not os.path.exists(output_folder):
        raise_validation_exception(f"Invalid path for '--output-folder': Path '{output_folder}' does not exist.")
    if source_dialect is None:
        raise_validation_exception("Value for '--source-dialect' must be provided.")
    engine = SqlglotEngine()
    supported_dialects = engine.supported_dialects
    if source_dialect not in supported_dialects:
        supported_dialects_description = ", ".join(supported_dialects)
        msg = f"Unsupported source dialect provided for '--source-dialect': '{source_dialect}' (supported: {supported_dialects_description})"
        raise_validation_exception(msg)

    lineage_generator(engine, source_dialect, input_source, output_folder)


@lakebridge.command
def configure_secrets(*, w: WorkspaceClient) -> None:
    """Setup reconciliation connection profile details as Secrets on Databricks Workspace"""
    recon_conf = ReconConfigPrompts(w)

    # Prompt for source
    source = recon_conf.prompt_source()

    logger.info(f"Setting up Scope, Secrets for `{source}` reconciliation")
    recon_conf.prompt_and_save_connection_details()


@lakebridge.command(is_unauthenticated=True)
def configure_database_profiler() -> None:
    """[Experimental] Install the lakebridge Assessment package"""
    prompts = Prompts()

    # Prompt for source system
    source_system = str(
        prompts.choice("Please select the source system you want to configure", PROFILER_SOURCE_SYSTEM)
    ).lower()

    # Create appropriate assessment configurator
    assessment = create_assessment_configurator(source_system=source_system, product_name="lakebridge", prompts=prompts)
    assessment.run()


@lakebridge.command
def install_transpile(
    *,
    w: WorkspaceClient,
    artifact: str | None = None,
    interactive: str | None = None,
    transpiler_repository: TranspilerRepository = TranspilerRepository.user_home(),
) -> None:
    """Install or upgrade the Lakebridge transpilers."""
    is_interactive = interactive_mode(interactive)
    ctx = ApplicationContext(w)
    ctx.add_user_agent_extra("cmd", "install-transpile")
    if artifact:
        ctx.add_user_agent_extra("artifact-overload", Path(artifact).name)
    user = w.current_user
    logger.debug(f"User: {user}")
    transpile_installer = installer(w, transpiler_repository, is_interactive=is_interactive)
    transpile_installer.run(module="transpile", artifact=artifact)


def interactive_mode(interactive: str | None, *, default: str = "auto", input_stream: TextIO = sys.stdin) -> bool:
    """Convert the raw '--interactive' argument into a boolean."""
    if interactive is None:
        interactive = default
    match interactive.lower():
        case "true":
            return True
        case "false":
            return False
        # Convention is that if the input_stream is a TTY, user interaction is allowed.
        case "auto":
            return input_stream.isatty()

    msg = f"Invalid value for '--interactive': {interactive!r} must be 'true', 'false' or 'auto'."
    raise_validation_exception(msg)


@lakebridge.command(is_unauthenticated=False)
def configure_reconcile(
    *,
    w: WorkspaceClient,
    transpiler_repository: TranspilerRepository = TranspilerRepository.user_home(),
) -> None:
    """Configure the Lakebridge reconciliation module"""
    ctx = ApplicationContext(w)
    ctx.add_user_agent_extra("cmd", "configure-reconcile")
    user = w.current_user
    logger.debug(f"User: {user}")
    if not w.config.warehouse_id:
        dbsql_id = _create_warehouse(w)
        w.config.warehouse_id = dbsql_id
    logger.debug(f"Warehouse ID used for configuring reconcile: {w.config.warehouse_id}.")
    reconcile_installer = installer(w, transpiler_repository, is_interactive=True)
    reconcile_installer.run(module="reconcile")


@lakebridge.command
def analyze(
    *,
    w: WorkspaceClient,
    source_directory: str | None = None,
    report_file: str | None = None,
    source_tech: str | None = None,
):
    """Run the Analyzer"""
    ctx = ApplicationContext(w)
    try:
        result = ctx.analyzer.run_analyzer(source_directory, report_file, source_tech)
        ctx.add_user_agent_extra("analyzer_source_tech", result.source_system)
    finally:
        exception_cls, _, _ = sys.exc_info()
        if exception_cls is not None:
            ctx.add_user_agent_extra("analyzer_error", exception_cls.__name__)

        ctx.add_user_agent_extra("cmd", "analyze")
        logger.debug(f"User: {ctx.current_user}")


if __name__ == "__main__":
    lakebridge()
    if is_in_debug():
        logger.setLevel(logging.DEBUG)
