import logging
from collections.abc import Callable, Generator, Sequence
from pathlib import Path
from unittest.mock import create_autospec, patch

import pytest
from databricks.labs.blueprint.installation import JsonObject, MockInstallation
from databricks.sdk import WorkspaceClient
from databricks.sdk.service import iam
from databricks.labs.blueprint.tui import MockPrompts
from databricks.labs.blueprint.wheels import ProductInfo, WheelsV2
from databricks.labs.lakebridge.config import (
    DatabaseConfig,
    LSPConfigOptionV1,
    LSPPromptMethod,
    LakebridgeConfiguration,
    ReconcileConfig,
    ReconcileMetadataConfig,
    TranspileConfig,
)
from databricks.labs.lakebridge.contexts.application import ApplicationContext
from databricks.labs.lakebridge.deployment.configurator import ResourceConfigurator
from databricks.labs.lakebridge.deployment.installation import WorkspaceInstallation
from databricks.labs.lakebridge.install import WorkspaceInstaller
from databricks.labs.lakebridge.reconcile.constants import ReconSourceType, ReconReportType
from databricks.labs.lakebridge.transpiler.installers import TranspilerInstaller
from databricks.labs.lakebridge.transpiler.repository import TranspilerRepository

from tests.unit.conftest import path_to_resource

RECONCILE_DATA_SOURCES = sorted([source_type.value for source_type in ReconSourceType])
RECONCILE_REPORT_TYPES = sorted([report_type.value for report_type in ReconReportType])


@pytest.fixture
def ws() -> WorkspaceClient:
    w = create_autospec(WorkspaceClient)
    w.current_user.me.side_effect = lambda: iam.User(
        user_name="me@example.com", groups=[iam.ComplexValue(display="admins")]
    )
    return w


SET_IT_LATER = ["Set it later"]
ALL_INSTALLED_DIALECTS_NO_LATER = sorted(["tsql", "snowflake"])
ALL_INSTALLED_DIALECTS = SET_IT_LATER + ALL_INSTALLED_DIALECTS_NO_LATER
TRANSPILERS_FOR_SNOWFLAKE_NO_LATER = sorted(["Remorph Community Transpiler", "Morpheus"])
TRANSPILERS_FOR_SNOWFLAKE = SET_IT_LATER + TRANSPILERS_FOR_SNOWFLAKE_NO_LATER
PATH_TO_TRANSPILER_CONFIG = "/some/path/to/config.yml"


@pytest.fixture()
def ws_installer() -> Generator[Callable[..., WorkspaceInstaller], None, None]:

    class TestWorkspaceInstaller(WorkspaceInstaller):
        def __init__(self, *args, **kwargs):
            # Ensure that the transpiler repository is mocked for unit tests instead of being the real thing.
            if "transpiler_repository" not in kwargs:
                kwargs["transpiler_repository"] = create_autospec(TranspilerRepository)
            # In these unit tests we have no transpilers to install by default.
            if "transpiler_installers" not in kwargs:
                kwargs["transpiler_installers"] = ()

            super().__init__(*args, **kwargs)

        def _all_installed_dialects(self):
            return ALL_INSTALLED_DIALECTS_NO_LATER

        def _transpilers_with_dialect(self, dialect):
            return TRANSPILERS_FOR_SNOWFLAKE_NO_LATER

        def _transpiler_config_path(self, transpiler):
            return PATH_TO_TRANSPILER_CONFIG

    def installer(*args, **kwargs) -> WorkspaceInstaller:
        return TestWorkspaceInstaller(*args, **kwargs)

    yield installer


def test_workspace_installer_run_raise_error_in_dbr(ws: WorkspaceClient) -> None:
    ctx = ApplicationContext(ws)
    environ = {"DATABRICKS_RUNTIME_VERSION": "8.3.x-scala2.12"}
    with pytest.raises(SystemExit):
        WorkspaceInstaller(
            ctx.workspace_client,
            ctx.prompts,
            ctx.installation,
            ctx.install_state,
            ctx.product_info,
            ctx.resource_configurator,
            ctx.workspace_installation,
            environ=environ,
        )


def test_workspace_installer_run_install_not_called_in_test(
    ws_installer: Callable[..., WorkspaceInstaller],
    ws: WorkspaceClient,
) -> None:
    ws_installation = create_autospec(WorkspaceInstallation)
    ctx = ApplicationContext(ws)
    ctx.replace(
        product_info=ProductInfo.for_testing(LakebridgeConfiguration),
        resource_configurator=create_autospec(ResourceConfigurator),
        workspace_installation=ws_installation,
    )

    provided_config = LakebridgeConfiguration()
    workspace_installer = ws_installer(
        ctx.workspace_client,
        ctx.prompts,
        ctx.installation,
        ctx.install_state,
        ctx.product_info,
        ctx.resource_configurator,
        ctx.workspace_installation,
    )

    returned_config = workspace_installer.run(module="transpile", config=provided_config)

    assert returned_config == provided_config
    ws_installation.install.assert_not_called()


def test_workspace_installer_run_install_called_with_provided_config(
    ws_installer: Callable[..., WorkspaceInstaller],
    ws: WorkspaceClient,
) -> None:
    ws_installation = create_autospec(WorkspaceInstallation)
    ctx = ApplicationContext(ws)
    ctx.replace(
        resource_configurator=create_autospec(ResourceConfigurator),
        workspace_installation=ws_installation,
    )
    provided_config = LakebridgeConfiguration()
    workspace_installer = ws_installer(
        ctx.workspace_client,
        ctx.prompts,
        ctx.installation,
        ctx.install_state,
        ctx.product_info,
        ctx.resource_configurator,
        ctx.workspace_installation,
    )

    returned_config = workspace_installer.run(module="transpile", config=provided_config)

    assert returned_config == provided_config
    ws_installation.install.assert_called_once_with(provided_config)


def test_configure_error_if_invalid_module_selected(ws: WorkspaceClient) -> None:
    ctx = ApplicationContext(ws)
    ctx.replace(
        resource_configurator=create_autospec(ResourceConfigurator),
        workspace_installation=create_autospec(WorkspaceInstallation),
    )
    workspace_installer = WorkspaceInstaller(
        ctx.workspace_client,
        ctx.prompts,
        ctx.installation,
        ctx.install_state,
        ctx.product_info,
        ctx.resource_configurator,
        ctx.workspace_installation,
    )

    with pytest.raises(ValueError):
        workspace_installer.configure(module="invalid_module")


def test_workspace_installer_run_install_called_with_generated_config(
    ws_installer: Callable[..., WorkspaceInstaller],
    ws: WorkspaceClient,
) -> None:
    prompts = MockPrompts(
        {
            r"Do you want to override the existing installation?": "no",
            r"Select the source dialect": str(ALL_INSTALLED_DIALECTS.index("snowflake")),
            r"Select the transpiler": str(TRANSPILERS_FOR_SNOWFLAKE.index("Morpheus")),
            r"Enter input SQL path.*": "/tmp/queries/snow",
            r"Enter output directory.*": "/tmp/queries/databricks",
            r"Enter error file path.*": "/tmp/queries/errors.log",
            r"Would you like to validate.*": "no",
            r"Open .* in the browser?": "no",
        }
    )
    installation = MockInstallation()
    ctx = ApplicationContext(ws)
    ctx.replace(
        prompts=prompts,
        installation=installation,
        resource_configurator=create_autospec(ResourceConfigurator),
        workspace_installation=create_autospec(WorkspaceInstallation),
    )

    workspace_installer = ws_installer(
        ctx.workspace_client,
        ctx.prompts,
        ctx.installation,
        ctx.install_state,
        ctx.product_info,
        ctx.resource_configurator,
        ctx.workspace_installation,
    )
    workspace_installer.run("transpile")
    installation.assert_file_written(
        "config.yml",
        {
            "catalog_name": "remorph",
            "transpiler_config_path": PATH_TO_TRANSPILER_CONFIG,
            "source_dialect": "snowflake",
            "input_source": "/tmp/queries/snow",
            "output_folder": "/tmp/queries/databricks",
            "error_file_path": "/tmp/queries/errors.log",
            "schema_name": "transpiler",
            "skip_validation": True,
            "version": 3,
        },
    )


def test_configure_transpile_no_existing_installation(
    ws_installer: Callable[..., WorkspaceInstaller],
    ws: WorkspaceClient,
) -> None:
    prompts = MockPrompts(
        {
            r"Do you want to override the existing installation?": "no",
            r"Select the source dialect": str(ALL_INSTALLED_DIALECTS.index("snowflake")),
            r"Select the transpiler": str(TRANSPILERS_FOR_SNOWFLAKE.index("Morpheus")),
            r"Enter input SQL path.*": "/tmp/queries/snow",
            r"Enter output directory.*": "/tmp/queries/databricks",
            r"Enter error file path.*": "/tmp/queries/errors.log",
            r"Would you like to validate.*": "no",
            r"Open .* in the browser?": "no",
        }
    )
    installation = MockInstallation()
    ctx = ApplicationContext(ws)
    ctx.replace(
        prompts=prompts,
        installation=installation,
        resource_configurator=create_autospec(ResourceConfigurator),
        workspace_installation=create_autospec(WorkspaceInstallation),
    )
    workspace_installer = ws_installer(
        ctx.workspace_client,
        ctx.prompts,
        ctx.installation,
        ctx.install_state,
        ctx.product_info,
        ctx.resource_configurator,
        ctx.workspace_installation,
    )

    config = workspace_installer.configure(module="transpile")
    expected_morph_config = TranspileConfig(
        transpiler_config_path=PATH_TO_TRANSPILER_CONFIG,
        transpiler_options=None,
        source_dialect="snowflake",
        input_source="/tmp/queries/snow",
        output_folder="/tmp/queries/databricks",
        error_file_path="/tmp/queries/errors.log",
        skip_validation=True,
        catalog_name="remorph",
        schema_name="transpiler",
    )
    expected_config = LakebridgeConfiguration(transpile=expected_morph_config)
    assert config == expected_config
    installation.assert_file_written(
        "config.yml",
        {
            "catalog_name": "remorph",
            "transpiler_config_path": PATH_TO_TRANSPILER_CONFIG,
            "input_source": "/tmp/queries/snow",
            "output_folder": "/tmp/queries/databricks",
            "error_file_path": "/tmp/queries/errors.log",
            "schema_name": "transpiler",
            "skip_validation": True,
            "source_dialect": "snowflake",
            "version": 3,
        },
    )


def test_configure_transpile_installation_no_override(ws: WorkspaceClient) -> None:
    prompts = MockPrompts(
        {
            r"Do you want to override the existing installation?": "no",
        }
    )
    ctx = ApplicationContext(ws)
    ctx.replace(
        prompts=prompts,
        resource_configurator=create_autospec(ResourceConfigurator),
        workspace_installation=create_autospec(WorkspaceInstallation),
        installation=MockInstallation(
            {
                "config.yml": {
                    "transpiler_config_path": PATH_TO_TRANSPILER_CONFIG,
                    "source_dialect": "snowflake",
                    "catalog_name": "transpiler_test",
                    "input_source": "sf_queries",
                    "output_folder": "out_dir",
                    "schema_name": "converter_test",
                    "sdk_config": {
                        "warehouse_id": "abc",
                    },
                    "version": 3,
                }
            }
        ),
    )

    workspace_installer = WorkspaceInstaller(
        ctx.workspace_client,
        ctx.prompts,
        ctx.installation,
        ctx.install_state,
        ctx.product_info,
        ctx.resource_configurator,
        ctx.workspace_installation,
    )
    remorph_config = workspace_installer.configure(module="transpile")
    assert remorph_config.transpile == TranspileConfig(
        transpiler_config_path=PATH_TO_TRANSPILER_CONFIG,
        source_dialect="snowflake",
        input_source="sf_queries",
        output_folder="out_dir",
        catalog_name="transpiler_test",
        schema_name="converter_test",
        sdk_config={"warehouse_id": "abc"},
    )


def test_configure_transpile_installation_config_error_continue_install(
    ws_installer: Callable[..., WorkspaceInstaller],
    ws: WorkspaceClient,
) -> None:
    prompts = MockPrompts(
        {
            r"Do you want to override the existing installation?": "yes",
            r"Select the source dialect": str(ALL_INSTALLED_DIALECTS.index("snowflake")),
            r"Select the transpiler": str(TRANSPILERS_FOR_SNOWFLAKE.index("Morpheus")),
            r"Enter input SQL path.*": "/tmp/queries/snow",
            r"Enter output directory.*": "/tmp/queries/databricks",
            r"Enter error file path.*": "/tmp/queries/errors.log",
            r"Would you like to validate.*": "no",
            r"Open .* in the browser?": "no",
        }
    )
    installation = MockInstallation(
        {
            "config.yml": {
                "invalid_transpiler": "some value",  # Invalid key
                "source_dialect": "snowflake",
                "catalog_name": "transpiler_test",
                "input_source": "sf_queries",
                "output_folder": "out_dir",
                "error_file_path": "error_log",
                "schema_name": "convertor_test",
                "sdk_config": {
                    "warehouse_id": "abc",
                },
                "version": 3,
            }
        }
    )
    ctx = ApplicationContext(ws)
    ctx.replace(
        prompts=prompts,
        installation=installation,
        resource_configurator=create_autospec(ResourceConfigurator),
        workspace_installation=create_autospec(WorkspaceInstallation),
    )
    workspace_installer = ws_installer(
        ctx.workspace_client,
        ctx.prompts,
        ctx.installation,
        ctx.install_state,
        ctx.product_info,
        ctx.resource_configurator,
        ctx.workspace_installation,
    )

    config = workspace_installer.configure(module="transpile")

    expected_morph_config = TranspileConfig(
        transpiler_config_path=PATH_TO_TRANSPILER_CONFIG,
        transpiler_options=None,
        source_dialect="snowflake",
        input_source="/tmp/queries/snow",
        output_folder="/tmp/queries/databricks",
        error_file_path="/tmp/queries/errors.log",
        skip_validation=True,
        catalog_name="remorph",
        schema_name="transpiler",
    )
    expected_config = LakebridgeConfiguration(transpile=expected_morph_config)
    assert config == expected_config
    installation.assert_file_written(
        "config.yml",
        {
            "transpiler_config_path": PATH_TO_TRANSPILER_CONFIG,
            "catalog_name": "remorph",
            "input_source": "/tmp/queries/snow",
            "output_folder": "/tmp/queries/databricks",
            "error_file_path": "/tmp/queries/errors.log",
            "schema_name": "transpiler",
            "skip_validation": True,
            "source_dialect": "snowflake",
            "version": 3,
        },
    )


@patch("webbrowser.open")
def test_configure_transpile_installation_with_no_validation(ws, ws_installer):
    prompts = MockPrompts(
        {
            r"Select the source dialect": ALL_INSTALLED_DIALECTS.index("snowflake"),
            r"Select the transpiler": TRANSPILERS_FOR_SNOWFLAKE.index("Morpheus"),
            r"Enter input SQL path.*": "/tmp/queries/snow",
            r"Enter output directory.*": "/tmp/queries/databricks",
            r"Enter error file path.*": "/tmp/queries/errors.log",
            r"Would you like to validate.*": "no",
            r"Open .* in the browser?": "yes",
        }
    )
    installation = MockInstallation()
    ctx = ApplicationContext(ws)
    ctx.replace(
        prompts=prompts,
        installation=installation,
        resource_configurator=create_autospec(ResourceConfigurator),
        workspace_installation=create_autospec(WorkspaceInstallation),
    )

    workspace_installer = ws_installer(
        ctx.workspace_client,
        ctx.prompts,
        ctx.installation,
        ctx.install_state,
        ctx.product_info,
        ctx.resource_configurator,
        ctx.workspace_installation,
    )

    config = workspace_installer.configure(module="transpile")

    expected_morph_config = TranspileConfig(
        transpiler_config_path=PATH_TO_TRANSPILER_CONFIG,
        transpiler_options=None,
        source_dialect="snowflake",
        input_source="/tmp/queries/snow",
        output_folder="/tmp/queries/databricks",
        error_file_path="/tmp/queries/errors.log",
        skip_validation=True,
        catalog_name="remorph",
        schema_name="transpiler",
    )
    expected_config = LakebridgeConfiguration(transpile=expected_morph_config)
    assert config == expected_config
    installation.assert_file_written(
        "config.yml",
        {
            "transpiler_config_path": PATH_TO_TRANSPILER_CONFIG,
            "catalog_name": "remorph",
            "input_source": "/tmp/queries/snow",
            "output_folder": "/tmp/queries/databricks",
            "error_file_path": "/tmp/queries/errors.log",
            "schema_name": "transpiler",
            "skip_validation": True,
            "source_dialect": "snowflake",
            "version": 3,
        },
    )


def test_configure_transpile_installation_with_validation_and_warehouse_id_from_prompt(
    ws_installer: Callable[..., WorkspaceInstaller],
    ws: WorkspaceClient,
) -> None:
    prompts = MockPrompts(
        {
            r"Select the source dialect": str(ALL_INSTALLED_DIALECTS.index("snowflake")),
            r"Select the transpiler": str(TRANSPILERS_FOR_SNOWFLAKE.index("Morpheus")),
            r"Enter input SQL path.*": "/tmp/queries/snow",
            r"Enter output directory.*": "/tmp/queries/databricks",
            r"Enter error file path.*": "/tmp/queries/errors.log",
            r"Would you like to validate.*": "yes",
            r"Do you want to use SQL Warehouse for validation?": "yes",
            r"Open .* in the browser?": "no",
        }
    )
    installation = MockInstallation()
    resource_configurator = create_autospec(ResourceConfigurator)
    resource_configurator.prompt_for_catalog_setup.return_value = "remorph_test"
    resource_configurator.prompt_for_schema_setup.return_value = "transpiler_test"
    resource_configurator.prompt_for_warehouse_setup.return_value = "w_id"

    ctx = ApplicationContext(ws)
    ctx.replace(
        prompts=prompts,
        installation=installation,
        resource_configurator=resource_configurator,
        workspace_installation=create_autospec(WorkspaceInstallation),
    )

    workspace_installer = ws_installer(
        ctx.workspace_client,
        ctx.prompts,
        ctx.installation,
        ctx.install_state,
        ctx.product_info,
        ctx.resource_configurator,
        ctx.workspace_installation,
    )

    config = workspace_installer.configure(module="transpile")

    expected_config = LakebridgeConfiguration(
        transpile=TranspileConfig(
            transpiler_config_path=PATH_TO_TRANSPILER_CONFIG,
            transpiler_options=None,
            source_dialect="snowflake",
            input_source="/tmp/queries/snow",
            output_folder="/tmp/queries/databricks",
            error_file_path="/tmp/queries/errors.log",
            catalog_name="remorph_test",
            schema_name="transpiler_test",
            sdk_config={"warehouse_id": "w_id"},
        )
    )
    assert config == expected_config
    installation.assert_file_written(
        "config.yml",
        {
            "transpiler_config_path": PATH_TO_TRANSPILER_CONFIG,
            "catalog_name": "remorph_test",
            "input_source": "/tmp/queries/snow",
            "output_folder": "/tmp/queries/databricks",
            "error_file_path": "/tmp/queries/errors.log",
            "schema_name": "transpiler_test",
            "sdk_config": {"warehouse_id": "w_id"},
            "source_dialect": "snowflake",
            "version": 3,
        },
    )


def test_configure_reconcile_installation_no_override(ws: WorkspaceClient) -> None:
    prompts = MockPrompts(
        {
            r"Do you want to override the existing installation?": "no",
        }
    )
    ctx = ApplicationContext(ws)
    ctx.replace(
        prompts=prompts,
        resource_configurator=create_autospec(ResourceConfigurator),
        workspace_installation=create_autospec(WorkspaceInstallation),
        installation=MockInstallation(
            {
                "reconcile.yml": {
                    "data_source": "snowflake",
                    "report_type": "all",
                    "secret_scope": "remorph_snowflake",
                    "database_config": {
                        "source_catalog": "snowflake_sample_data",
                        "source_schema": "tpch_sf1000",
                        "target_catalog": "tpch",
                        "target_schema": "1000gb",
                    },
                    "metadata_config": {
                        "catalog": "remorph",
                        "schema": "reconcile",
                        "volume": "reconcile_volume",
                    },
                    "version": 1,
                }
            }
        ),
    )
    workspace_installer = WorkspaceInstaller(
        ctx.workspace_client,
        ctx.prompts,
        ctx.installation,
        ctx.install_state,
        ctx.product_info,
        ctx.resource_configurator,
        ctx.workspace_installation,
    )
    with pytest.raises(SystemExit):
        workspace_installer.configure(module="reconcile")


def test_configure_reconcile_installation_config_error_continue_install(ws: WorkspaceClient) -> None:
    prompts = MockPrompts(
        {
            r"Select the Data Source": str(RECONCILE_DATA_SOURCES.index("oracle")),
            r"Select the report type": str(RECONCILE_REPORT_TYPES.index("all")),
            r"Enter Secret scope name to store .* connection details / secrets": "remorph_oracle",
            r"Enter source database name for .*": "tpch_sf1000",
            r"Enter target catalog name for Databricks": "tpch",
            r"Enter target schema name for Databricks": "1000gb",
            r"Open .* in the browser?": "no",
        }
    )
    installation = MockInstallation(
        {
            "reconcile.yml": {
                "source_dialect": "oracle",  # Invalid key
                "report_type": "all",
                "secret_scope": "remorph_oracle",
                "database_config": {
                    "source_schema": "tpch_sf1000",
                    "target_catalog": "tpch",
                    "target_schema": "1000gb",
                },
                "metadata_config": {
                    "catalog": "remorph",
                    "schema": "reconcile",
                    "volume": "reconcile_volume",
                },
                "version": 1,
            }
        }
    )

    resource_configurator = create_autospec(ResourceConfigurator)
    resource_configurator.prompt_for_catalog_setup.return_value = "remorph"
    resource_configurator.prompt_for_schema_setup.return_value = "reconcile"
    resource_configurator.prompt_for_volume_setup.return_value = "reconcile_volume"

    ctx = ApplicationContext(ws)
    ctx.replace(
        prompts=prompts,
        installation=installation,
        resource_configurator=resource_configurator,
        workspace_installation=create_autospec(WorkspaceInstallation),
    )

    workspace_installer = WorkspaceInstaller(
        ctx.workspace_client,
        ctx.prompts,
        ctx.installation,
        ctx.install_state,
        ctx.product_info,
        ctx.resource_configurator,
        ctx.workspace_installation,
    )
    config = workspace_installer.configure(module="reconcile")

    expected_config = LakebridgeConfiguration(
        reconcile=ReconcileConfig(
            data_source="oracle",
            report_type="all",
            secret_scope="remorph_oracle",
            database_config=DatabaseConfig(
                source_schema="tpch_sf1000",
                target_catalog="tpch",
                target_schema="1000gb",
            ),
            metadata_config=ReconcileMetadataConfig(
                catalog="remorph",
                schema="reconcile",
                volume="reconcile_volume",
            ),
        )
    )
    assert config == expected_config
    installation.assert_file_written(
        "reconcile.yml",
        {
            "data_source": "oracle",
            "report_type": "all",
            "secret_scope": "remorph_oracle",
            "database_config": {
                "source_schema": "tpch_sf1000",
                "target_catalog": "tpch",
                "target_schema": "1000gb",
            },
            "metadata_config": {
                "catalog": "remorph",
                "schema": "reconcile",
                "volume": "reconcile_volume",
            },
            "version": 1,
        },
    )


@patch("webbrowser.open")
def test_configure_reconcile_no_existing_installation(ws: WorkspaceClient) -> None:
    prompts = MockPrompts(
        {
            r"Select the Data Source": str(RECONCILE_DATA_SOURCES.index("snowflake")),
            r"Select the report type": str(RECONCILE_REPORT_TYPES.index("all")),
            r"Enter Secret scope name to store .* connection details / secrets": "remorph_snowflake",
            r"Enter source catalog name for .*": "snowflake_sample_data",
            r"Enter source schema name for .*": "tpch_sf1000",
            r"Enter target catalog name for Databricks": "tpch",
            r"Enter target schema name for Databricks": "1000gb",
            r"Open .* in the browser?": "yes",
        }
    )
    installation = MockInstallation()
    resource_configurator = create_autospec(ResourceConfigurator)
    resource_configurator.prompt_for_catalog_setup.return_value = "remorph"
    resource_configurator.prompt_for_schema_setup.return_value = "reconcile"
    resource_configurator.prompt_for_volume_setup.return_value = "reconcile_volume"

    ctx = ApplicationContext(ws)
    ctx.replace(
        prompts=prompts,
        installation=installation,
        resource_configurator=resource_configurator,
        workspace_installation=create_autospec(WorkspaceInstallation),
    )

    workspace_installer = WorkspaceInstaller(
        ctx.workspace_client,
        ctx.prompts,
        ctx.installation,
        ctx.install_state,
        ctx.product_info,
        ctx.resource_configurator,
        ctx.workspace_installation,
    )
    config = workspace_installer.configure(module="reconcile")

    expected_config = LakebridgeConfiguration(
        reconcile=ReconcileConfig(
            data_source="snowflake",
            report_type="all",
            secret_scope="remorph_snowflake",
            database_config=DatabaseConfig(
                source_schema="tpch_sf1000",
                target_catalog="tpch",
                target_schema="1000gb",
                source_catalog="snowflake_sample_data",
            ),
            metadata_config=ReconcileMetadataConfig(
                catalog="remorph",
                schema="reconcile",
                volume="reconcile_volume",
            ),
        )
    )
    assert config == expected_config
    installation.assert_file_written(
        "reconcile.yml",
        {
            "data_source": "snowflake",
            "report_type": "all",
            "secret_scope": "remorph_snowflake",
            "database_config": {
                "source_catalog": "snowflake_sample_data",
                "source_schema": "tpch_sf1000",
                "target_catalog": "tpch",
                "target_schema": "1000gb",
            },
            "metadata_config": {
                "catalog": "remorph",
                "schema": "reconcile",
                "volume": "reconcile_volume",
            },
            "version": 1,
        },
    )


def test_configure_all_override_installation(
    ws_installer: Callable[..., WorkspaceInstaller],
    ws: WorkspaceClient,
) -> None:
    prompts = MockPrompts(
        {
            r"Do you want to override the existing installation?": "yes",
            r"Select the source dialect": str(ALL_INSTALLED_DIALECTS.index("snowflake")),
            r"Select the transpiler": str(TRANSPILERS_FOR_SNOWFLAKE.index("Morpheus")),
            r"Enter input SQL path.*": "/tmp/queries/snow",
            r"Enter output directory.*": "/tmp/queries/databricks",
            r"Enter error file path.*": "/tmp/queries/errors.log",
            r"Would you like to validate.*": "no",
            r"Open .* in the browser?": "no",
            r"Select the Data Source": str(RECONCILE_DATA_SOURCES.index("snowflake")),
            r"Select the report type": str(RECONCILE_REPORT_TYPES.index("all")),
            r"Enter Secret scope name to store .* connection details / secrets": "remorph_snowflake",
            r"Enter source catalog name for .*": "snowflake_sample_data",
            r"Enter source schema name for .*": "tpch_sf1000",
            r"Enter target catalog name for Databricks": "tpch",
            r"Enter target schema name for Databricks": "1000gb",
        }
    )
    installation = MockInstallation(
        {
            "config.yml": {
                "transpiler_config_path": PATH_TO_TRANSPILER_CONFIG,
                "source_dialect": "snowflake",
                "catalog_name": "transpiler_test",
                "input_source": "sf_queries",
                "output_folder": "out_dir",
                "error_file_path": "error_log.log",
                "schema_name": "convertor_test",
                "sdk_config": {
                    "warehouse_id": "abc",
                },
                "version": 3,
            },
            "reconcile.yml": {
                "data_source": "snowflake",
                "report_type": "all",
                "secret_scope": "remorph_snowflake",
                "database_config": {
                    "source_catalog": "snowflake_sample_data",
                    "source_schema": "tpch_sf1000",
                    "target_catalog": "tpch",
                    "target_schema": "1000gb",
                },
                "metadata_config": {
                    "catalog": "remorph",
                    "schema": "reconcile",
                    "volume": "reconcile_volume",
                },
                "version": 1,
            },
        }
    )

    resource_configurator = create_autospec(ResourceConfigurator)
    resource_configurator.prompt_for_catalog_setup.return_value = "remorph"
    resource_configurator.prompt_for_schema_setup.return_value = "reconcile"
    resource_configurator.prompt_for_volume_setup.return_value = "reconcile_volume"

    ctx = ApplicationContext(ws)
    ctx.replace(
        prompts=prompts,
        installation=installation,
        resource_configurator=resource_configurator,
        workspace_installation=create_autospec(WorkspaceInstallation),
    )

    workspace_installer = ws_installer(
        ctx.workspace_client,
        ctx.prompts,
        ctx.installation,
        ctx.install_state,
        ctx.product_info,
        ctx.resource_configurator,
        ctx.workspace_installation,
    )

    config = workspace_installer.configure(module="all")

    expected_transpile_config = TranspileConfig(
        transpiler_config_path=PATH_TO_TRANSPILER_CONFIG,
        transpiler_options=None,
        source_dialect="snowflake",
        input_source="/tmp/queries/snow",
        output_folder="/tmp/queries/databricks",
        error_file_path="/tmp/queries/errors.log",
        skip_validation=True,
        catalog_name="remorph",
        schema_name="transpiler",
    )

    expected_reconcile_config = ReconcileConfig(
        data_source="snowflake",
        report_type="all",
        secret_scope="remorph_snowflake",
        database_config=DatabaseConfig(
            source_schema="tpch_sf1000",
            target_catalog="tpch",
            target_schema="1000gb",
            source_catalog="snowflake_sample_data",
        ),
        metadata_config=ReconcileMetadataConfig(
            catalog="remorph",
            schema="reconcile",
            volume="reconcile_volume",
        ),
    )
    expected_config = LakebridgeConfiguration(transpile=expected_transpile_config, reconcile=expected_reconcile_config)
    assert config == expected_config
    installation.assert_file_written(
        "config.yml",
        {
            "transpiler_config_path": PATH_TO_TRANSPILER_CONFIG,
            "catalog_name": "remorph",
            "input_source": "/tmp/queries/snow",
            "output_folder": "/tmp/queries/databricks",
            "error_file_path": "/tmp/queries/errors.log",
            "schema_name": "transpiler",
            "skip_validation": True,
            "source_dialect": "snowflake",
            "version": 3,
        },
    )

    installation.assert_file_written(
        "reconcile.yml",
        {
            "data_source": "snowflake",
            "report_type": "all",
            "secret_scope": "remorph_snowflake",
            "database_config": {
                "source_catalog": "snowflake_sample_data",
                "source_schema": "tpch_sf1000",
                "target_catalog": "tpch",
                "target_schema": "1000gb",
            },
            "metadata_config": {
                "catalog": "remorph",
                "schema": "reconcile",
                "volume": "reconcile_volume",
            },
            "version": 1,
        },
    )


def test_runs_upgrades_on_more_recent_version(
    ws_installer: Callable[..., WorkspaceInstaller],
    ws: WorkspaceClient,
) -> None:
    installation = MockInstallation(
        {
            'version.json': {'version': '0.3.0', 'wheel': '...', 'date': '...'},
            'state.json': {
                'resources': {
                    'dashboards': {'Reconciliation Metrics': 'abc'},
                    'jobs': {'Reconciliation Runner': '12345'},
                }
            },
            'config.yml': {
                "transpiler-config-path": PATH_TO_TRANSPILER_CONFIG,
                "source_dialect": "snowflake",
                "catalog_name": "upgrades",
                "input_source": "queries",
                "output_folder": "out",
                "error_file_path": "errors.log",
                "schema_name": "test",
                "sdk_config": {
                    "warehouse_id": "dummy",
                },
                "version": 3,
            },
        }
    )

    ctx = ApplicationContext(ws)
    prompts = MockPrompts(
        {
            r"Do you want to override the existing installation?": "yes",
            r"Select the source dialect": str(ALL_INSTALLED_DIALECTS.index("snowflake")),
            r"Select the transpiler": str(TRANSPILERS_FOR_SNOWFLAKE.index("Morpheus")),
            r"Enter input SQL path.*": "/tmp/queries/snow",
            r"Enter output directory.*": "/tmp/queries/databricks",
            r"Enter error file.*": "/tmp/queries/errors.log",
            r"Would you like to validate.*": "no",
            r"Open .* in the browser?": "no",
        }
    )
    wheels = create_autospec(WheelsV2)

    mock_workspace_installation = create_autospec(WorkspaceInstallation)

    ctx.replace(
        prompts=prompts,
        installation=installation,
        resource_configurator=create_autospec(ResourceConfigurator),
        workspace_installation=mock_workspace_installation,
        wheels=wheels,
    )

    workspace_installer = ws_installer(
        ctx.workspace_client,
        ctx.prompts,
        ctx.installation,
        ctx.install_state,
        ctx.product_info,
        ctx.resource_configurator,
        ctx.workspace_installation,
    )

    workspace_installer.run("transpile")

    mock_workspace_installation.install.assert_called_once_with(
        LakebridgeConfiguration(
            transpile=TranspileConfig(
                transpiler_config_path=PATH_TO_TRANSPILER_CONFIG,
                transpiler_options=None,
                source_dialect="snowflake",
                input_source="/tmp/queries/snow",
                output_folder="/tmp/queries/databricks",
                error_file_path="/tmp/queries/errors.log",
                catalog_name="remorph",
                schema_name="transpiler",
                skip_validation=True,
            )
        )
    )


def test_runs_and_stores_confirm_config_option(
    ws_installer: Callable[..., WorkspaceInstaller],
    ws: WorkspaceClient,
    tmp_path: Path,
) -> None:
    prompts = MockPrompts(
        {
            r"Select the source dialect": str(ALL_INSTALLED_DIALECTS.index("snowflake")),
            r"Select the transpiler": str(TRANSPILERS_FOR_SNOWFLAKE.index("Remorph Community Transpiler")),
            r"Do you want to use the experimental Databricks generator ?": "yes",
            r"Enter input SQL path.*": "/tmp/queries/snow",
            r"Enter output directory.*": "/tmp/queries/databricks",
            r"Enter error file path.*": "/tmp/queries/errors.log",
            r"Would you like to validate.*": "yes",
            r"Do you want to use SQL Warehouse for validation?": "yes",
            r"Open .* in the browser?": "no",
        }
    )
    installation = MockInstallation()
    resource_configurator = create_autospec(ResourceConfigurator)
    resource_configurator.prompt_for_catalog_setup.return_value = "remorph_test"
    resource_configurator.prompt_for_schema_setup.return_value = "transpiler_test"
    resource_configurator.prompt_for_warehouse_setup.return_value = "w_id"

    ctx = ApplicationContext(ws)
    ctx.replace(
        prompts=prompts,
        installation=installation,
        resource_configurator=resource_configurator,
        workspace_installation=create_autospec(WorkspaceInstallation),
    )

    class _TranspilerRepository(TranspilerRepository):
        def __init__(self) -> None:
            super().__init__(tmp_path / "labs")
            self._transpilers_path = Path(path_to_resource("transpiler_configs"))

        def transpilers_path(self) -> Path:
            return self._transpilers_path

    workspace_installer = ws_installer(
        ctx.workspace_client,
        ctx.prompts,
        ctx.installation,
        ctx.install_state,
        ctx.product_info,
        ctx.resource_configurator,
        ctx.workspace_installation,
        transpiler_repository=_TranspilerRepository(),
    )

    config = workspace_installer.configure(module="transpile")

    expected_config = LakebridgeConfiguration(
        transpile=TranspileConfig(
            transpiler_config_path=PATH_TO_TRANSPILER_CONFIG,
            transpiler_options={"-experimental": True},
            source_dialect="snowflake",
            input_source="/tmp/queries/snow",
            output_folder="/tmp/queries/databricks",
            error_file_path="/tmp/queries/errors.log",
            catalog_name="remorph_test",
            schema_name="transpiler_test",
            sdk_config={"warehouse_id": "w_id"},
        )
    )
    assert config == expected_config
    installation.assert_file_written(
        "config.yml",
        {
            "transpiler_config_path": PATH_TO_TRANSPILER_CONFIG,
            "transpiler_options": {'-experimental': True},
            "catalog_name": "remorph_test",
            "input_source": "/tmp/queries/snow",
            "output_folder": "/tmp/queries/databricks",
            "error_file_path": "/tmp/queries/errors.log",
            "schema_name": "transpiler_test",
            "sdk_config": {"warehouse_id": "w_id"},
            "source_dialect": "snowflake",
            "version": 3,
        },
    )


class _StubTranspilerRepository(TranspilerRepository):
    def __init__(self, labs_path: Path, config_options: Sequence[LSPConfigOptionV1]) -> None:
        super().__init__(labs_path)
        self._config_options = config_options

    def transpiler_config_options(self, transpiler_name: str, source_dialect: str) -> list[LSPConfigOptionV1]:
        return list(self._config_options)


def test_runs_and_stores_force_config_option(
    ws_installer: Callable[..., WorkspaceInstaller],
    ws: WorkspaceClient,
    tmp_path: Path,
) -> None:
    prompts = MockPrompts(
        {
            r"Select the source dialect": str(ALL_INSTALLED_DIALECTS.index("snowflake")),
            r"Select the transpiler": str(TRANSPILERS_FOR_SNOWFLAKE.index("Remorph Community Transpiler")),
            r"Enter input SQL path.*": "/tmp/queries/snow",
            r"Enter output directory.*": "/tmp/queries/databricks",
            r"Enter error file path.*": "/tmp/queries/errors.log",
            r"Would you like to validate.*": "yes",
            r"Do you want to use SQL Warehouse for validation?": "yes",
            r"Open .* in the browser?": "no",
        }
    )
    installation = MockInstallation()
    resource_configurator = create_autospec(ResourceConfigurator)
    resource_configurator.prompt_for_catalog_setup.return_value = "remorph_test"
    resource_configurator.prompt_for_schema_setup.return_value = "transpiler_test"
    resource_configurator.prompt_for_warehouse_setup.return_value = "w_id"

    ctx = ApplicationContext(ws)
    ctx.replace(
        prompts=prompts,
        installation=installation,
        resource_configurator=resource_configurator,
        workspace_installation=create_autospec(WorkspaceInstallation),
    )

    transpiler_repository = _StubTranspilerRepository(
        tmp_path / "labs", config_options=(LSPConfigOptionV1(flag="-XX", method=LSPPromptMethod.FORCE, default=1254),)
    )

    workspace_installer = ws_installer(
        ctx.workspace_client,
        ctx.prompts,
        ctx.installation,
        ctx.install_state,
        ctx.product_info,
        ctx.resource_configurator,
        ctx.workspace_installation,
        transpiler_repository=transpiler_repository,
    )

    config = workspace_installer.configure(module="transpile")

    expected_config = LakebridgeConfiguration(
        transpile=TranspileConfig(
            transpiler_config_path=PATH_TO_TRANSPILER_CONFIG,
            transpiler_options={"-XX": 1254},
            source_dialect="snowflake",
            input_source="/tmp/queries/snow",
            output_folder="/tmp/queries/databricks",
            error_file_path="/tmp/queries/errors.log",
            catalog_name="remorph_test",
            schema_name="transpiler_test",
            sdk_config={"warehouse_id": "w_id"},
        )
    )
    assert config == expected_config
    installation.assert_file_written(
        "config.yml",
        {
            "transpiler_config_path": PATH_TO_TRANSPILER_CONFIG,
            "transpiler_options": {'-XX': 1254},
            "catalog_name": "remorph_test",
            "input_source": "/tmp/queries/snow",
            "output_folder": "/tmp/queries/databricks",
            "error_file_path": "/tmp/queries/errors.log",
            "schema_name": "transpiler_test",
            "sdk_config": {"warehouse_id": "w_id"},
            "source_dialect": "snowflake",
            "version": 3,
        },
    )


def test_runs_and_stores_question_config_option(
    ws_installer: Callable[..., WorkspaceInstaller],
    ws: WorkspaceClient,
    tmp_path: Path,
) -> None:
    prompts = MockPrompts(
        {
            r"Select the source dialect": str(ALL_INSTALLED_DIALECTS.index("snowflake")),
            r"Select the transpiler": str(TRANSPILERS_FOR_SNOWFLAKE.index("Remorph Community Transpiler")),
            r"Max number of heaps:": "1254",
            r"Enter input SQL path.*": "/tmp/queries/snow",
            r"Enter output directory.*": "/tmp/queries/databricks",
            r"Enter error file path.*": "/tmp/queries/errors.log",
            r"Would you like to validate.*": "yes",
            r"Do you want to use SQL Warehouse for validation?": "yes",
            r"Open .* in the browser?": "no",
        }
    )
    installation = MockInstallation()
    resource_configurator = create_autospec(ResourceConfigurator)
    resource_configurator.prompt_for_catalog_setup.return_value = "remorph_test"
    resource_configurator.prompt_for_schema_setup.return_value = "transpiler_test"
    resource_configurator.prompt_for_warehouse_setup.return_value = "w_id"

    ctx = ApplicationContext(ws)
    ctx.replace(
        prompts=prompts,
        installation=installation,
        resource_configurator=resource_configurator,
        workspace_installation=create_autospec(WorkspaceInstallation),
    )

    transpiler_repository = _StubTranspilerRepository(
        tmp_path / "labs",
        config_options=(LSPConfigOptionV1(flag="-XX", method=LSPPromptMethod.QUESTION, prompt="Max number of heaps:"),),
    )

    workspace_installer = ws_installer(
        ctx.workspace_client,
        ctx.prompts,
        ctx.installation,
        ctx.install_state,
        ctx.product_info,
        ctx.resource_configurator,
        ctx.workspace_installation,
        transpiler_repository=transpiler_repository,
    )

    config = workspace_installer.configure(module="transpile")

    expected_config = LakebridgeConfiguration(
        transpile=TranspileConfig(
            transpiler_config_path=PATH_TO_TRANSPILER_CONFIG,
            transpiler_options={"-XX": "1254"},
            source_dialect="snowflake",
            input_source="/tmp/queries/snow",
            output_folder="/tmp/queries/databricks",
            error_file_path="/tmp/queries/errors.log",
            catalog_name="remorph_test",
            schema_name="transpiler_test",
            sdk_config={"warehouse_id": "w_id"},
        )
    )
    assert config == expected_config
    installation.assert_file_written(
        "config.yml",
        {
            "transpiler_config_path": PATH_TO_TRANSPILER_CONFIG,
            "transpiler_options": {'-XX': "1254"},
            "catalog_name": "remorph_test",
            "input_source": "/tmp/queries/snow",
            "output_folder": "/tmp/queries/databricks",
            "error_file_path": "/tmp/queries/errors.log",
            "schema_name": "transpiler_test",
            "sdk_config": {"warehouse_id": "w_id"},
            "source_dialect": "snowflake",
            "version": 3,
        },
    )


def test_runs_and_stores_choice_config_option(
    ws_installer: Callable[..., WorkspaceInstaller],
    ws: WorkspaceClient,
    tmp_path: Path,
) -> None:
    prompts = MockPrompts(
        {
            r"Select the source dialect": str(ALL_INSTALLED_DIALECTS.index("snowflake")),
            r"Select the transpiler": str(TRANSPILERS_FOR_SNOWFLAKE.index("Remorph Community Transpiler")),
            r"Select currency:": "2",
            r"Enter input SQL path.*": "/tmp/queries/snow",
            r"Enter output directory.*": "/tmp/queries/databricks",
            r"Enter error file path.*": "/tmp/queries/errors.log",
            r"Would you like to validate.*": "yes",
            r"Do you want to use SQL Warehouse for validation?": "yes",
            r"Open .* in the browser?": "no",
        }
    )
    installation = MockInstallation()
    resource_configurator = create_autospec(ResourceConfigurator)
    resource_configurator.prompt_for_catalog_setup.return_value = "remorph_test"
    resource_configurator.prompt_for_schema_setup.return_value = "transpiler_test"
    resource_configurator.prompt_for_warehouse_setup.return_value = "w_id"

    ctx = ApplicationContext(ws)
    ctx.replace(
        prompts=prompts,
        installation=installation,
        resource_configurator=resource_configurator,
        workspace_installation=create_autospec(WorkspaceInstallation),
    )

    transpiler_repository = _StubTranspilerRepository(
        tmp_path / "labs",
        config_options=(
            LSPConfigOptionV1(
                flag="-currency",
                method=LSPPromptMethod.CHOICE,
                prompt="Select currency:",
                choices=["CHF", "EUR", "GBP", "USD"],
            ),
        ),
    )
    workspace_installer = ws_installer(
        ctx.workspace_client,
        ctx.prompts,
        ctx.installation,
        ctx.install_state,
        ctx.product_info,
        ctx.resource_configurator,
        ctx.workspace_installation,
        transpiler_repository=transpiler_repository,
    )

    config = workspace_installer.configure(module="transpile")

    expected_config = LakebridgeConfiguration(
        transpile=TranspileConfig(
            transpiler_config_path=PATH_TO_TRANSPILER_CONFIG,
            transpiler_options={"-currency": "GBP"},
            source_dialect="snowflake",
            input_source="/tmp/queries/snow",
            output_folder="/tmp/queries/databricks",
            error_file_path="/tmp/queries/errors.log",
            catalog_name="remorph_test",
            schema_name="transpiler_test",
            sdk_config={"warehouse_id": "w_id"},
        )
    )
    assert config == expected_config
    installation.assert_file_written(
        "config.yml",
        {
            "transpiler_config_path": PATH_TO_TRANSPILER_CONFIG,
            "transpiler_options": {'-currency': "GBP"},
            "catalog_name": "remorph_test",
            "input_source": "/tmp/queries/snow",
            "output_folder": "/tmp/queries/databricks",
            "error_file_path": "/tmp/queries/errors.log",
            "schema_name": "transpiler_test",
            "sdk_config": {"warehouse_id": "w_id"},
            "source_dialect": "snowflake",
            "version": 3,
        },
    )


@pytest.mark.parametrize(("installed_transpilers",), (({"foo", "bar"},), ({},)))
def test_installer_detects_installed_transpilers(
    ws_installer: Callable[..., WorkspaceInstaller], ws: WorkspaceClient, installed_transpilers: set[str], caplog
) -> None:
    """Check detection of whether transpilers are already installed or not."""
    mock_repository = create_autospec(TranspilerRepository)
    mock_repository.all_transpiler_names.return_value = installed_transpilers
    ctx = ApplicationContext(ws)

    installer = ws_installer(
        ctx.workspace_client,
        ctx.prompts,
        ctx.installation,
        ctx.install_state,
        ctx.product_info,
        ctx.resource_configurator,
        ctx.workspace_installation,
        transpiler_repository=mock_repository,
    )

    with caplog.at_level(logging.INFO):
        installer.upgrade_installed_transpilers()

    if installed_transpilers:
        info_messages = [log.message for log in caplog.records if log.levelno == logging.INFO]
        assert f"Detected installed transpilers: {sorted(installed_transpilers)}" in info_messages


def test_installer_upgrade_installed_transpilers(
    ws_installer: Callable[..., WorkspaceInstaller], ws: WorkspaceClient
) -> None:
    """Check that during install we attempt to upgrade any known transpilers that are already installed."""

    # The setup here is:
    #   - 'foo' and 'bar' are installed already.
    #   - 'bar' and 'baz' are known transpilers.
    # It should therefore try to install/upgrade bar but _not_ baz.

    mock_repository = create_autospec(TranspilerRepository)
    mock_repository.all_transpiler_names.return_value = {"foo", "bar"}
    ctx = ApplicationContext(ws).replace(
        product_info=ProductInfo.for_testing(LakebridgeConfiguration),
        prompts=(MockPrompts({r"Do you want to override the existing installation?": "no"})),
        installation=MockInstallation({"config.yml": {"version": 3}}),
    )

    class MockTranspilerInstaller(TranspilerInstaller):
        def __init__(self, repository: TranspilerRepository, name: str) -> None:
            super().__init__(repository)
            self._name = name
            self.installed = False

        def can_install(self, artifact: Path) -> bool:
            return False

        @property
        def name(self) -> str:
            return self._name

        def install(self, artifact: Path | None = None) -> bool:
            self.installed = True
            return True

        def mock_factory(self, repository: TranspilerRepository) -> TranspilerInstaller:
            assert repository is self._transpiler_repository
            return self

    bar_installer = MockTranspilerInstaller(mock_repository, "bar")
    baz_installer = MockTranspilerInstaller(mock_repository, "baz")

    installer = ws_installer(
        ctx.workspace_client,
        ctx.prompts,
        ctx.installation,
        ctx.install_state,
        ctx.product_info,
        ctx.resource_configurator,
        ctx.workspace_installation,
        transpiler_repository=mock_repository,
        transpiler_installers=(baz_installer.mock_factory, bar_installer.mock_factory),
    )
    upgraded_something = installer.upgrade_installed_transpilers()

    assert upgraded_something, "Expected to upgrade at least one transpiler"
    assert bar_installer.installed, "Expected 'bar' transpiler to be upgraded"
    assert not baz_installer.installed, "Did not expect 'baz' transpiler to be upgraded"


@pytest.mark.parametrize("test_upgrade", (True, False))
def test_installer_upgrade_configure_if_changed(
    ws_installer: Callable[..., WorkspaceInstaller],
    ws: WorkspaceClient,
    test_upgrade: bool,
) -> None:
    """Check that during an upgrade we reconfigure if any transpiler upgrades occurred."""

    # The setup here is:
    #   - 'foo' is the installed transpiler, and we will attempt to upgrade it.
    #   - parameterized on whether the upgrade is necessary.
    #   - if it was, we expect the prompt-adjusted configuration to be returned.

    mock_repository = create_autospec(TranspilerRepository)
    mock_repository.all_transpiler_names.return_value = {"foo"}
    prior_source_dialect = "original_dialect"
    prior_configuration: JsonObject = {
        "version": 3,
        "transpiler_config_path": PATH_TO_TRANSPILER_CONFIG,
        "source_dialect": prior_source_dialect,
        "input_source": "sf_queries",
        "output_folder": "out_dir",
        "error_file_path": "error_log.log",
        "skip_validation": True,
        "catalog_name": "remorph",
        "schema_name": "transpiler",
    }
    mock_installation = MockInstallation({"config.yml": prior_configuration})
    ctx = ApplicationContext(ws).replace(
        product_info=ProductInfo.for_testing(LakebridgeConfiguration),
        prompts=MockPrompts(
            {
                r"Do you want to override the existing installation?": "yes",
                r"Select the source dialect": "2",
                r"Select the transpiler": "1",
                r"Enter .*": "/tmp/updated",
                r"Would you like to validate.*": "no",
                r"Open config file .* in the browser?": "no",
            }
        ),
        installation=mock_installation,
    )

    class MockTranspilerInstaller(TranspilerInstaller):
        def __init__(self, repository: TranspilerRepository) -> None:
            super().__init__(repository)
            self.installed = False

        def can_install(self, artifact: Path) -> bool:
            return False

        @property
        def name(self) -> str:
            return "foo"

        def install(self, artifact: Path | None = None) -> bool:
            self.installed = True
            return test_upgrade

    installer = ws_installer(
        ctx.workspace_client,
        ctx.prompts,
        ctx.installation,
        ctx.install_state,
        ctx.product_info,
        ctx.resource_configurator,
        ctx.workspace_installation,
        transpiler_repository=mock_repository,
        transpiler_installers=(MockTranspilerInstaller,),
    )
    upgraded_something = installer.upgrade_installed_transpilers()

    assert upgraded_something == test_upgrade
    if test_upgrade:
        expected_configuration = {
            **prior_configuration,
            "version": 3,
            # These were updated by the configuration.
            "source_dialect": "tsql",
            "input_source": "/tmp/updated",
            "output_folder": "/tmp/updated",
            "error_file_path": "/tmp/updated",
        }
        mock_installation.assert_file_written("config.yml", expected_configuration)


def test_no_reconfigure_if_noninteractive(
    ws_installer: Callable[..., WorkspaceInstaller],
    ws: WorkspaceClient,
    caplog,
) -> None:
    """Check that when non-interactive we do not attempt to reconfigure if there is already a config."""

    no_prompts_available = MockPrompts({})

    ctx = ApplicationContext(ws).replace(
        product_info=ProductInfo.for_testing(LakebridgeConfiguration),
        prompts=no_prompts_available,
        installation=MockInstallation(
            {
                "config.yml": {
                    "version": 3,
                    "transpiler_config_path": PATH_TO_TRANSPILER_CONFIG,
                    "source_dialect": "frobnicat",
                    "input_source": "sf_queries",
                    "output_folder": "out_dir",
                    "error_file_path": "error_log.log",
                    "skip_validation": True,
                    "catalog_name": "remorph",
                    "schema_name": "transpiler",
                }
            }
        ),
    )

    installer = ws_installer(
        ctx.workspace_client,
        ctx.prompts,
        ctx.installation,
        ctx.install_state,
        ctx.product_info,
        ctx.resource_configurator,
        ctx.workspace_installation,
        is_interactive=False,
    )
    with caplog.at_level(logging.DEBUG):
        config = installer.run(module="transpile")

    assert config.transpile is not None
    expected_log_message = "Installation is not interactive, keeping existing configuration."
    assert any(expected_log_message in log.message for log in caplog.records if log.levelno == logging.DEBUG)


def test_no_configure_if_noninteractive(
    ws_installer: Callable[..., WorkspaceInstaller],
    ws: WorkspaceClient,
    caplog,
) -> None:
    """Check that when non-interactive we do not attempt configuration, even if there is no existing config."""

    no_prompts_available = MockPrompts({})

    ctx = ApplicationContext(ws).replace(
        product_info=ProductInfo.for_testing(LakebridgeConfiguration),
        prompts=no_prompts_available,
        installation=MockInstallation({}),
    )

    installer = ws_installer(
        ctx.workspace_client,
        ctx.prompts,
        ctx.installation,
        ctx.install_state,
        ctx.product_info,
        ctx.resource_configurator,
        ctx.workspace_installation,
        is_interactive=False,
    )
    with caplog.at_level(logging.WARNING):
        config = installer.run(module="transpile")

    assert config.transpile is None
    expected_log_message = "Installation is not interactive, skipping configuration of transpilers."
    assert any(expected_log_message in log.message for log in caplog.records if log.levelno == logging.WARNING)
