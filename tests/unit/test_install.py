import datetime as dt
import json
import os
import shutil
from pathlib import Path
from unittest.mock import create_autospec, patch

import pytest
from databricks.labs.blueprint.installation import MockInstallation
from databricks.sdk import WorkspaceClient
from databricks.sdk.service import iam
from databricks.labs.blueprint.tui import MockPrompts
from databricks.labs.lakebridge.config import (
    RemorphConfigs,
    ReconcileConfig,
    DatabaseConfig,
    ReconcileMetadataConfig,
    LSPConfigOptionV1,
    LSPPromptMethod,
)
from databricks.labs.lakebridge.contexts.application import ApplicationContext
from databricks.labs.lakebridge.deployment.configurator import ResourceConfigurator
from databricks.labs.lakebridge.deployment.installation import WorkspaceInstallation
from databricks.labs.lakebridge.install import WorkspaceInstaller, TranspilerInstaller
from databricks.labs.lakebridge.config import TranspileConfig
from databricks.labs.blueprint.wheels import ProductInfo, WheelsV2
from databricks.labs.lakebridge.reconcile.constants import ReconSourceType, ReconReportType

from tests.unit.conftest import path_to_resource

RECONCILE_DATA_SOURCES = sorted([source_type.value for source_type in ReconSourceType])
RECONCILE_REPORT_TYPES = sorted([report_type.value for report_type in ReconReportType])


@pytest.fixture
def ws():
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
def ws_installer():

    class TestWorkspaceInstaller(WorkspaceInstaller):

        # TODO the below 'install_xxx' methods currently fail
        # (because the artifact is either missing or invalid)
        # TODO remove this once they are available and healthy !!!
        @classmethod
        def install_bladebridge(cls, artifact: Path | None = None):
            pass

        @classmethod
        def install_morpheus(cls, artifact: Path | None = None):
            pass

        def _all_installed_dialects(self):
            return ALL_INSTALLED_DIALECTS_NO_LATER

        def _transpilers_with_dialect(self, dialect):
            return TRANSPILERS_FOR_SNOWFLAKE_NO_LATER

        def _transpiler_config_path(self, transpiler):
            return PATH_TO_TRANSPILER_CONFIG

    def installer(*args, **kwargs) -> WorkspaceInstaller:
        return TestWorkspaceInstaller(*args, **kwargs)

    yield installer


def test_workspace_installer_run_raise_error_in_dbr(ws):
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


def test_workspace_installer_run_install_not_called_in_test(ws_installer, ws):
    ws_installation = create_autospec(WorkspaceInstallation)
    ctx = ApplicationContext(ws)
    ctx.replace(
        product_info=ProductInfo.for_testing(RemorphConfigs),
        resource_configurator=create_autospec(ResourceConfigurator),
        workspace_installation=ws_installation,
    )

    provided_config = RemorphConfigs()
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


def test_workspace_installer_run_install_called_with_provided_config(ws_installer, ws):
    ws_installation = create_autospec(WorkspaceInstallation)
    ctx = ApplicationContext(ws)
    ctx.replace(
        resource_configurator=create_autospec(ResourceConfigurator),
        workspace_installation=ws_installation,
    )
    provided_config = RemorphConfigs()
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


def test_configure_error_if_invalid_module_selected(ws):
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


def test_workspace_installer_run_install_called_with_generated_config(ws_installer, ws):
    prompts = MockPrompts(
        {
            r"Do you want to override the existing installation?": "no",
            r"Select the source dialect": ALL_INSTALLED_DIALECTS.index("snowflake"),
            r"Select the transpiler": TRANSPILERS_FOR_SNOWFLAKE.index("Morpheus"),
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


def test_configure_transpile_no_existing_installation(ws_installer, ws):
    prompts = MockPrompts(
        {
            r"Do you want to override the existing installation?": "no",
            r"Select the source dialect": ALL_INSTALLED_DIALECTS.index("snowflake"),
            r"Select the transpiler": TRANSPILERS_FOR_SNOWFLAKE.index("Morpheus"),
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
    expected_config = RemorphConfigs(transpile=expected_morph_config)
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


@patch("databricks.labs.lakebridge.install.WorkspaceInstaller.install_bladebridge")
@patch("databricks.labs.lakebridge.install.WorkspaceInstaller.install_morpheus")
def test_configure_transpile_installation_no_override(mock_install_morpheus, mock_install_bladebridge, ws):
    mock_install_bladebridge.return_value = None
    mock_install_morpheus.return_value = None

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


def test_configure_transpile_installation_config_error_continue_install(ws_installer, ws):
    prompts = MockPrompts(
        {
            r"Do you want to override the existing installation?": "yes",
            r"Select the source dialect": ALL_INSTALLED_DIALECTS.index("snowflake"),
            r"Select the transpiler": TRANSPILERS_FOR_SNOWFLAKE.index("Morpheus"),
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
    expected_config = RemorphConfigs(transpile=expected_morph_config)
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
    expected_config = RemorphConfigs(transpile=expected_morph_config)
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


def test_configure_transpile_installation_with_validation_and_warehouse_id_from_prompt(ws_installer, ws):
    prompts = MockPrompts(
        {
            r"Select the source dialect": ALL_INSTALLED_DIALECTS.index("snowflake"),
            r"Select the transpiler": TRANSPILERS_FOR_SNOWFLAKE.index("Morpheus"),
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

    expected_config = RemorphConfigs(
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


def test_configure_reconcile_installation_no_override(ws):
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


def test_configure_reconcile_installation_config_error_continue_install(ws):
    prompts = MockPrompts(
        {
            r"Select the Data Source": RECONCILE_DATA_SOURCES.index("oracle"),
            r"Select the report type": RECONCILE_REPORT_TYPES.index("all"),
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

    expected_config = RemorphConfigs(
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
def test_configure_reconcile_no_existing_installation(ws):
    prompts = MockPrompts(
        {
            r"Select the Data Source": RECONCILE_DATA_SOURCES.index("snowflake"),
            r"Select the report type": RECONCILE_REPORT_TYPES.index("all"),
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

    expected_config = RemorphConfigs(
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


def test_configure_all_override_installation(ws_installer, ws):
    prompts = MockPrompts(
        {
            r"Do you want to override the existing installation?": "yes",
            r"Select the source dialect": ALL_INSTALLED_DIALECTS.index("snowflake"),
            r"Select the transpiler": TRANSPILERS_FOR_SNOWFLAKE.index("Morpheus"),
            r"Enter input SQL path.*": "/tmp/queries/snow",
            r"Enter output directory.*": "/tmp/queries/databricks",
            r"Enter error file path.*": "/tmp/queries/errors.log",
            r"Would you like to validate.*": "no",
            r"Open .* in the browser?": "no",
            r"Select the Data Source": RECONCILE_DATA_SOURCES.index("snowflake"),
            r"Select the report type": RECONCILE_REPORT_TYPES.index("all"),
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
    expected_config = RemorphConfigs(transpile=expected_transpile_config, reconcile=expected_reconcile_config)
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


def test_runs_upgrades_on_more_recent_version(ws_installer, ws):
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
            r"Select the source dialect": ALL_INSTALLED_DIALECTS.index("snowflake"),
            r"Select the transpiler": TRANSPILERS_FOR_SNOWFLAKE.index("Morpheus"),
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
        RemorphConfigs(
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


def test_runs_and_stores_confirm_config_option(ws_installer, ws):
    prompts = MockPrompts(
        {
            r"Select the source dialect": ALL_INSTALLED_DIALECTS.index("snowflake"),
            r"Select the transpiler": TRANSPILERS_FOR_SNOWFLAKE.index("Remorph Community Transpiler"),
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

    workspace_installer = ws_installer(
        ctx.workspace_client,
        ctx.prompts,
        ctx.installation,
        ctx.install_state,
        ctx.product_info,
        ctx.resource_configurator,
        ctx.workspace_installation,
    )

    with (
        patch(
            "databricks.labs.lakebridge.install.TranspilerInstaller.transpilers_path",
            return_value=Path(path_to_resource("transpiler_configs")),
        ),
    ):

        config = workspace_installer.configure(module="transpile")

        expected_config = RemorphConfigs(
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


def test_runs_and_stores_force_config_option(ws_installer, ws):
    prompts = MockPrompts(
        {
            r"Select the source dialect": ALL_INSTALLED_DIALECTS.index("snowflake"),
            r"Select the transpiler": TRANSPILERS_FOR_SNOWFLAKE.index("Remorph Community Transpiler"),
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

    TranspilerInstaller.transpiler_config_options = lambda a, b: [
        LSPConfigOptionV1(flag="-XX", method=LSPPromptMethod.FORCE, default=1254)
    ]

    config = workspace_installer.configure(module="transpile")

    expected_config = RemorphConfigs(
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


def test_runs_and_stores_question_config_option(ws_installer, ws):
    prompts = MockPrompts(
        {
            r"Select the source dialect": ALL_INSTALLED_DIALECTS.index("snowflake"),
            r"Select the transpiler": TRANSPILERS_FOR_SNOWFLAKE.index("Remorph Community Transpiler"),
            r"Max number of heaps:": 1254,
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

    TranspilerInstaller.transpiler_config_options = lambda a, b: [
        LSPConfigOptionV1(flag="-XX", method=LSPPromptMethod.QUESTION, prompt="Max number of heaps:")
    ]

    config = workspace_installer.configure(module="transpile")

    expected_config = RemorphConfigs(
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


def test_runs_and_stores_choice_config_option(ws_installer, ws):
    prompts = MockPrompts(
        {
            r"Select the source dialect": ALL_INSTALLED_DIALECTS.index("snowflake"),
            r"Select the transpiler": TRANSPILERS_FOR_SNOWFLAKE.index("Remorph Community Transpiler"),
            r"Select currency:": 2,
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

    TranspilerInstaller.transpiler_config_options = lambda a, b: [
        LSPConfigOptionV1(
            flag="-currency",
            method=LSPPromptMethod.CHOICE,
            prompt="Select currency:",
            choices=["CHF", "EUR", "GBP", "USD"],
        )
    ]

    config = workspace_installer.configure(module="transpile")

    expected_config = RemorphConfigs(
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


def test_store_product_state(tmp_path) -> None:
    """Verify the product state is stored after installing is correct."""

    class MockTranspilerInstaller(TranspilerInstaller):
        @classmethod
        def store_product_state(cls, product_path: Path, version: str) -> None:
            cls._store_product_state(product_path, version)

    # Store the product state, capturing the time before and after so we can verify the timestamp it puts in there.
    before = dt.datetime.now(tz=dt.timezone.utc)
    MockTranspilerInstaller.store_product_state(tmp_path, "1.2.3")
    after = dt.datetime.now(tz=dt.timezone.utc)

    # Load the state that was just stored.
    with (tmp_path / "state" / "version.json").open("r", encoding="utf-8") as f:
        stored_state = json.load(f)

    # Verify the timestamp first.
    stored_date = stored_state["date"]
    parsed_date = dt.datetime.fromisoformat(stored_date)
    assert parsed_date.tzinfo is not None, "Stored date should be timezone-aware."
    assert before <= parsed_date <= after, f"Stored date {stored_date} is not within the expected range."

    # Verify the rest, now that we've checked the timestamp.
    expected_state = {
        "version": "v1.2.3",
        "date": stored_date,
    }
    assert stored_state == expected_state


@pytest.fixture
def no_java(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure that (temporarily) no 'java' binary can be found in the environment."""
    found_java = shutil.which("java")
    while found_java is not None:
        # Java is installed, so we need to figure out how to remove it from the path.
        # (We loop here to handle cases where multiple java binaries are available via the PATH.)
        java_directory = Path(found_java).parent
        search_path = os.environ.get("PATH", os.defpath).split(os.pathsep)
        updated_path = os.pathsep.join(p for p in search_path if p and Path(p) != java_directory)
        assert (
            search_path != updated_path
        ), f"Did not find {java_directory} in {search_path}, but 'java' was found at {found_java}."

        # Set the modified PATH without the directory where 'java' was found.
        monkeypatch.setenv("PATH", os.pathsep.join(updated_path))

        # Check again if 'java' is still found.
        found_java = shutil.which("java")


def test_java_version_with_java_missing(no_java: None) -> None:
    """Verify the Java version check handles Java missing entirely."""
    expected_missing = WorkspaceInstaller.find_java()
    assert expected_missing is None


class FriendOfWorkspaceInstaller(WorkspaceInstaller):
    """A friend class to access protected methods for testing purposes."""

    @classmethod
    def parse_java_version(cls, output: str) -> tuple[int, int, int, int] | None:
        return cls._parse_java_version(output)


@pytest.mark.parametrize(
    ("version", "expected"),
    (
        # Real examples.
        pytest.param("1.8.0_452", None, id="1.8.0_452"),
        pytest.param("11.0.27", (11, 0, 27, 0), id="11.0.27"),
        pytest.param("17.0.15", (17, 0, 15, 0), id="17.0.15"),
        pytest.param("21.0.7", (21, 0, 7, 0), id="21.0.7"),
        pytest.param("24.0.1", (24, 0, 1, 0), id="24.0.1"),
        # All digits.
        pytest.param("1.2.3.4", (1, 2, 3, 4), id="1.2.3.4"),
        # Trailing zeros can be omitted.
        pytest.param("1.2.3", (1, 2, 3, 0), id="1.2.3"),
        pytest.param("1.2", (1, 2, 0, 0), id="1.2"),
        pytest.param("1", (1, 0, 0, 0), id="1"),
        # Another edge case.
        pytest.param("", None, id="empty string"),
    ),
)
def test_java_version_parse(version: str, expected: tuple[int, int, int, int] | None) -> None:
    """Verify that the Java version parsing works correctly."""
    # Format reference: https://docs.oracle.com/en/java/javase/11/install/version-string-format.html
    version_output = f'openjdk version "{version}" 2025-06-19'
    parsed = FriendOfWorkspaceInstaller.parse_java_version(version_output)
    assert parsed == expected


def test_java_version_parse_missing() -> None:
    """Verify that we return None when the version is missing."""
    version_output = "Nothing in here that looks like a version."
    parsed = FriendOfWorkspaceInstaller.parse_java_version(version_output)
    assert parsed is None
