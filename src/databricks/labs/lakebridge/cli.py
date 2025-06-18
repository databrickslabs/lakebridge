import asyncio
import dataclasses
import itertools
import json
import os
import time
from pathlib import Path
from typing import NoReturn, cast

from databricks.sdk.core import with_user_agent_extra
from databricks.sdk.service.sql import CreateWarehouseRequestWarehouseType
from databricks.sdk import WorkspaceClient

from databricks.labs.blueprint.cli import App
from databricks.labs.blueprint.entrypoint import get_logger
from databricks.labs.blueprint.installation import JsonValue
from databricks.labs.blueprint.tui import Prompts

from databricks.labs.bladespector.analyzer import Analyzer


from databricks.labs.lakebridge.assessments.configure_assessment import (
    create_assessment_configurator,
    PROFILER_SOURCE_SYSTEM,
)

from databricks.labs.lakebridge.__about__ import __version__
from databricks.labs.lakebridge.config import TranspileConfig, LSPConfigOptionV1
from databricks.labs.lakebridge.contexts.application import ApplicationContext
from databricks.labs.lakebridge.helpers.recon_config_utils import ReconConfigPrompts
from databricks.labs.lakebridge.helpers.telemetry_utils import make_alphanum_or_semver
from databricks.labs.lakebridge.install import WorkspaceInstaller
from databricks.labs.lakebridge.install import TranspilerInstaller
from databricks.labs.lakebridge.reconcile.runner import ReconcileRunner
from databricks.labs.lakebridge.lineage import lineage_generator
from databricks.labs.lakebridge.reconcile.recon_config import RECONCILE_OPERATION_NAME, AGG_RECONCILE_OPERATION_NAME
from databricks.labs.lakebridge.transpiler.execute import transpile as do_transpile


from databricks.labs.lakebridge.transpiler.lsp.lsp_engine import LSPConfig
from databricks.labs.lakebridge.transpiler.sqlglot.sqlglot_engine import SqlglotEngine
from databricks.labs.lakebridge.transpiler.transpile_engine import TranspileEngine

from databricks.labs.lakebridge.transpiler.transpile_status import ErrorSeverity

lakebridge = App(__file__)
logger = get_logger(__file__)


def raise_validation_exception(msg: str) -> NoReturn:
    raise ValueError(msg)


def _installer(ws: WorkspaceClient) -> WorkspaceInstaller:
    app_context = ApplicationContext(_verify_workspace_client(ws))
    return WorkspaceInstaller(
        app_context.workspace_client,
        app_context.prompts,
        app_context.installation,
        app_context.install_state,
        app_context.product_info,
        app_context.resource_configurator,
        app_context.workspace_installation,
    )


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


def _verify_workspace_client(ws: WorkspaceClient) -> WorkspaceClient:
    """
    [Private] Verifies and updates the workspace client configuration.
    """

    # Using reflection to set right value for _product_info for telemetry
    product_info = getattr(ws.config, '_product_info')
    if product_info[0] != "lakebridge":
        setattr(ws.config, '_product_info', ('lakebridge', __version__))

    return ws


@lakebridge.command
def transpile(
    w: WorkspaceClient,
    transpiler_config_path: str | None = None,
    source_dialect: str | None = None,
    input_source: str | None = None,
    output_folder: str | None = None,
    error_file_path: str | None = None,
    skip_validation: str | None = None,
    catalog_name: str | None = None,
    schema_name: str | None = None,
):
    """Transpiles source dialect to databricks dialect"""
    ctx = ApplicationContext(w)
    logger.debug(f"Application transpiler config: {ctx.transpile_config}")
    checker = _TranspileConfigChecker(ctx.transpile_config, ctx.prompts)
    checker.check_input_source(input_source)
    checker.check_source_dialect(source_dialect)
    checker.check_transpiler_config_path(transpiler_config_path)
    checker.check_transpiler_config_options()
    checker.check_output_folder(output_folder)
    checker.check_error_file_path(error_file_path)
    checker.check_skip_validation(skip_validation)
    checker.check_catalog_name(catalog_name)
    checker.check_schema_name(schema_name)
    config, engine = checker.check()
    result = asyncio.run(_transpile(ctx, config, engine))
    # DO NOT Modify this print statement, it is used by the CLI to display results in GO Table Template
    print(json.dumps(result))


class _TranspileConfigChecker:

    def __init__(self, config: TranspileConfig | None, prompts: Prompts):
        if not config:
            raise SystemExit("Installed transpile config not found. Please install lakebridge transpile first.")
        self._config: TranspileConfig = config
        self._prompts = prompts

    def check_input_source(self, input_source: str | None):
        if input_source == "None":
            input_source = None
        if not input_source:
            input_source = self._config.input_source
        if not input_source:
            input_source = self._prompts.question("Enter input SQL path (directory/file)")
            input_source = input_source.strip()
        if not input_source:
            raise_validation_exception("Missing '--input-source'")
        if not os.path.exists(input_source):
            raise_validation_exception(f"Invalid value for '--input-source': Path '{input_source}' does not exist.")
        logger.debug(f"Setting input_source to '{input_source}'")
        self._config = dataclasses.replace(self._config, input_source=input_source)

    def check_source_dialect(self, source_dialect: str | None):
        if source_dialect == "None":
            source_dialect = None
        if not source_dialect:
            source_dialect = self._config.source_dialect
        all_dialects = sorted(TranspilerInstaller.all_dialects())
        if source_dialect and source_dialect not in all_dialects:
            logger.error(f"'{source_dialect}' is not a supported dialect. Selecting a supported one...")
            source_dialect = None
        if not source_dialect:
            source_dialect = self._prompts.choice("Select the source dialect:", all_dialects)
        if not source_dialect:
            raise_validation_exception("Missing '--source-dialect'")
        logger.debug(f"Setting source_dialect to '{source_dialect}'")
        self._config = dataclasses.replace(self._config, source_dialect=source_dialect)

    def check_transpiler_config_path(self, transpiler_config_path: str | None):
        if transpiler_config_path == "None":
            transpiler_config_path = None
        if not transpiler_config_path:
            transpiler_config_path = self._config.transpiler_config_path
        # we allow pointing to a loose transpiler config (i.e. not installed under .databricks)
        if transpiler_config_path:
            if not os.path.exists(transpiler_config_path):
                logger.error(f"The transpiler configuration does not exist '{transpiler_config_path}'.")
                transpiler_config_path = None
        if transpiler_config_path:
            config = LSPConfig.load(Path(transpiler_config_path))
            if self._config.source_dialect not in config.remorph.dialects:
                logger.error(f"The configured transpiler does not support dialect '{self._config.source_dialect}'.")
                transpiler_config_path = None
        if not transpiler_config_path:
            transpiler_names = TranspilerInstaller.transpilers_with_dialect(cast(str, self._config.source_dialect))
            if len(transpiler_names) > 1:
                transpiler_name = self._prompts.choice("Select the transpiler:", list(transpiler_names))
            else:
                transpiler_name = next(name for name in transpiler_names)
                logger.info(f"Lakebridge will use the {transpiler_name} transpiler")
            transpiler_config_path = str(TranspilerInstaller.transpiler_config_path(transpiler_name))
        logger.debug(f"Setting transpiler_config_path to '{transpiler_config_path}'")
        self._config = dataclasses.replace(self._config, transpiler_config_path=cast(str, transpiler_config_path))

    def check_transpiler_config_options(self):
        lsp_config = LSPConfig.load(Path(self._config.transpiler_config_path))
        options_to_configure = lsp_config.options_for_dialect(self._config.source_dialect) or []
        transpiler_options = self._config.transpiler_options or {}
        if len(options_to_configure) == 0:
            transpiler_options = None
        else:
            # TODO delete stale options ?
            for option in options_to_configure:
                self._check_transpiler_config_option(option, transpiler_options)
        logger.debug(f"Setting transpiler_options to {transpiler_options}")
        self._config = dataclasses.replace(self._config, transpiler_options=transpiler_options)

    def _check_transpiler_config_option(self, option: LSPConfigOptionV1, values: dict[str, JsonValue]):
        if option.flag in values.keys():
            return
        values[option.flag] = option.prompt_for_value(self._prompts)

    def check_output_folder(self, output_folder: str | None):
        output_folder = output_folder if output_folder else self._config.output_folder
        if not output_folder:
            raise_validation_exception("Missing '--output-folder'")
        if not os.path.exists(output_folder):
            os.makedirs(output_folder, exist_ok=True)
        logger.debug(f"Setting output_folder to '{output_folder}'")
        self._config = dataclasses.replace(self._config, output_folder=output_folder)

    def check_error_file_path(self, error_file_path: str | None):
        error_file_path = error_file_path if error_file_path else self._config.error_file_path
        if not error_file_path or error_file_path == "None":
            raise_validation_exception("Missing '--error-file-path'")
        if error_file_path == "errors.log":
            error_file_path = str(Path.cwd() / "errors.log")
        if not os.path.exists(Path(error_file_path).parent):
            os.makedirs(Path(error_file_path).parent, exist_ok=True)

        logger.debug(f"Setting error_file_path to '{error_file_path}'")
        self._config = dataclasses.replace(self._config, error_file_path=error_file_path)

    def check_skip_validation(self, skip_validation_str: str | None):
        skip_validation: bool | None = None
        if skip_validation_str == "None":
            skip_validation_str = None
        if skip_validation_str is not None:
            if skip_validation_str.lower() not in {"true", "false"}:
                raise_validation_exception(
                    f"Invalid value for '--skip-validation': '{skip_validation_str}' is not one of 'true', 'false'."
                )
            skip_validation = skip_validation_str.lower() == "true"
        if skip_validation is None:
            skip_validation = self._config.skip_validation
        if skip_validation is None:
            skip_validation = self._prompts.confirm(
                "Would you like to validate the syntax and semantics of the transpiled queries?"
            )
        logger.debug(f"Setting skip_validation to '{skip_validation}'")
        self._config = dataclasses.replace(self._config, skip_validation=skip_validation)

    def check_catalog_name(self, catalog_name: str | None):
        if self._config.skip_validation:
            return
        if catalog_name == "None":
            catalog_name = None
        if not catalog_name:
            catalog_name = self._config.catalog_name
        if not catalog_name:
            raise_validation_exception(
                "Missing '--catalog-name', please run 'databricks labs lakebridge install-transpile' to configure one"
            )
        logger.debug(f"Setting catalog_name to '{catalog_name}'")
        self._config = dataclasses.replace(self._config, catalog_name=catalog_name)

    def check_schema_name(self, schema_name: str | None):
        if self._config.skip_validation:
            return
        if schema_name == "None":
            schema_name = None
        if not schema_name:
            schema_name = self._config.schema_name
        if not schema_name:
            raise_validation_exception(
                "Missing '--schema-name', please run 'databricks labs lakebridge install-transpile' to configure one"
            )
        logger.debug(f"Setting schema_name to '{schema_name}'")
        self._config = dataclasses.replace(self._config, schema_name=schema_name)

    def check(self) -> tuple[TranspileConfig, TranspileEngine]:
        logger.debug(f"Checking config: {self!s}")
        # not using os.path.exists because it sometimes fails mysteriously...
        transpiler_path = self._config.transpiler_path
        if not transpiler_path or not transpiler_path.exists():
            raise_validation_exception(
                f"Invalid value for '--transpiler-config-path': Path '{self._config.transpiler_config_path}' does not exist."
            )
        engine = TranspileEngine.load_engine(transpiler_path)
        engine.check_source_dialect(self._config.source_dialect)
        if not self._config.input_source or not os.path.exists(self._config.input_source):
            raise_validation_exception(
                f"Invalid value for '--input-source': Path '{self._config.input_source}' does not exist."
            )
        # 'transpiled' will be used as output_folder if not specified
        # 'errors.log' will be used as errors file if not specified
        return self._config, engine


async def _transpile(ctx: ApplicationContext, config: TranspileConfig, engine: TranspileEngine):
    """Transpiles source dialect to databricks dialect"""
    with_user_agent_extra("cmd", "execute-transpile")
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


def _override_workspace_client_config(ctx: ApplicationContext, overrides: dict[str, str] | None):
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
def reconcile(w: WorkspaceClient):
    """[EXPERIMENTAL] Reconciles source to Databricks datasets"""
    with_user_agent_extra("cmd", "execute-reconcile")
    ctx = ApplicationContext(w)
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
def aggregates_reconcile(w: WorkspaceClient):
    """[EXPERIMENTAL] Reconciles Aggregated source to Databricks datasets"""
    with_user_agent_extra("cmd", "execute-aggregates-reconcile")
    ctx = ApplicationContext(w)
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
def generate_lineage(w: WorkspaceClient, source_dialect: str, input_source: str, output_folder: str):
    """[Experimental] Generates a lineage of source SQL files or folder"""
    ctx = ApplicationContext(w)
    logger.debug(f"User: {ctx.current_user}")
    engine = SqlglotEngine()
    engine.check_source_dialect(source_dialect)
    if not input_source or not os.path.exists(input_source):
        raise_validation_exception(f"Invalid value for '--input-source': Path '{input_source}' does not exist.")
    if not os.path.exists(output_folder) or output_folder in {None, ""}:
        raise_validation_exception(f"Invalid value for '--output-folder': Path '{output_folder}' does not exist.")

    lineage_generator(engine, source_dialect, input_source, output_folder)


@lakebridge.command
def configure_secrets(w: WorkspaceClient):
    """Setup reconciliation connection profile details as Secrets on Databricks Workspace"""
    recon_conf = ReconConfigPrompts(w)

    # Prompt for source
    source = recon_conf.prompt_source()

    logger.info(f"Setting up Scope, Secrets for `{source}` reconciliation")
    recon_conf.prompt_and_save_connection_details()


@lakebridge.command(is_unauthenticated=True)
def configure_database_profiler():
    """[Experimental] Install the lakebridge Assessment package"""
    prompts = Prompts()

    # Prompt for source system
    source_system = str(
        prompts.choice("Please select the source system you want to configure", PROFILER_SOURCE_SYSTEM)
    ).lower()

    # Create appropriate assessment configurator
    assessment = create_assessment_configurator(source_system=source_system, product_name="lakebridge", prompts=prompts)
    assessment.run()


@lakebridge.command()
def install_transpile(w: WorkspaceClient, artifact: str | None = None):
    """Install the lakebridge Transpilers"""
    with_user_agent_extra("cmd", "install-transpile")
    user = w.current_user
    logger.debug(f"User: {user}")
    installer = _installer(w)
    installer.run(module="transpile", artifact=artifact)


@lakebridge.command(is_unauthenticated=False)
def configure_reconcile(w: WorkspaceClient):
    """Configure the lakebridge Reconcile Package"""
    with_user_agent_extra("cmd", "configure-reconcile")
    user = w.current_user
    logger.debug(f"User: {user}")
    dbsql_id = _create_warehouse(w)
    w.config.warehouse_id = dbsql_id
    installer = _installer(w)
    installer.run(module="reconcile")
    _remove_warehouse(w, dbsql_id)


@lakebridge.command()
def analyze(w: WorkspaceClient, source_directory: str, report_file: str):
    """Run the Analyzer"""
    with_user_agent_extra("cmd", "analyze")
    ctx = ApplicationContext(w)
    prompts = ctx.prompts
    output_file = report_file
    input_folder = source_directory
    source_tech = prompts.choice("Select the source technology", Analyzer.supported_source_technologies())
    with_user_agent_extra("analyzer_source_tech", make_alphanum_or_semver(source_tech))
    user = ctx.current_user
    logger.debug(f"User: {user}")
    Analyzer.analyze(Path(input_folder), Path(output_file), source_tech)


if __name__ == "__main__":
    lakebridge()
