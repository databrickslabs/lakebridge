from collections.abc import Generator, Callable
from unittest.mock import create_autospec, patch, ANY, MagicMock
from pathlib import Path
import dataclasses
import os.path

import pytest
import yaml

from databricks.labs.lakebridge import cli

from databricks.labs.blueprint.tui import MockPrompts
from databricks.labs.lakebridge.config import TranspileConfig
from databricks.sdk import WorkspaceClient

from databricks.labs.lakebridge.contexts.application import ApplicationContext
from tests.unit.conftest import path_to_resource

TRANSPILERS_PATH = Path(__file__).parent.parent / "resources" / "transpiler_configs"


@pytest.fixture(name="transpiler_config_path")
def stubbed_transpiler_config_path(tmp_path: Path) -> Path:
    """Path to a stubbed LSP transpiler configuration file."""
    # Refer to LSPConfig.load() for the expected structure; there is no .save() method.
    lsp_configuration = {
        "remorph": {
            "version": 1,
            "name": "Stubbed LSP Transpiler Configuration",
            "dialects": ["snowflake"],
            "command_line": ["/usr/bin/true"],
        },
        "options": {
            "all": [
                {
                    "flag": "-experimental",
                    "method": "CONFIRM",
                    "prompt": "Do you want to use the experimental Databricks generator ?",
                }
            ]
        },
    }

    config_path = tmp_path / "lsp_config.yml"
    with config_path.open("w") as f:
        yaml.dump(lsp_configuration, f)
    return config_path


def test_transpile_with_missing_installation(transpiler_config_path: Path) -> None:
    with (
        patch("databricks.labs.lakebridge.cli.ApplicationContext", autospec=True) as mock_app_context,
        pytest.raises(SystemExit),
    ):
        workspace_client = create_autospec(WorkspaceClient)
        mock_app_context.return_value.workspace_client = workspace_client
        mock_app_context.return_value.transpile_config = None
        cli.transpile(
            workspace_client,
            str(transpiler_config_path),
            "snowflake",
            "/path/to/sql/file.sql",
            "/path/to/output",
            "/path/to/errors.log",
            "true",
            "my_catalog",
            "my_schema",
        )


@pytest.fixture
def mock_cli_for_transpile(
    mock_workspace_client: WorkspaceClient, transpiler_config_path: Path
) -> Generator[tuple[WorkspaceClient, TranspileConfig, Callable[[TranspileConfig], None], MagicMock], None, None]:
    mock_transpile = MagicMock(return_value=({}, []))

    async def do_transpile(*args, **kwargs):
        return mock_transpile(*args, **kwargs)

    prompts = MockPrompts(
        {
            "Do you want to use the experimental.*": "no",
            "Enter output directory": "/path/to/output/folder",
            "Select the source dialect.*": "21",  # snowflake
            "Select the transpiler.*": "1",  # morpheus
        }
    )
    mock_app_context = create_autospec(ApplicationContext)
    with (
        patch("os.path.exists", side_effect=lambda path: path != "invalid_path"),
        patch("databricks.labs.lakebridge.install.TranspilerInstaller.transpilers_path", return_value=TRANSPILERS_PATH),
        patch("databricks.labs.lakebridge.cli.do_transpile", new=do_transpile),
        patch("databricks.labs.lakebridge.cli.ApplicationContext", mock_app_context),
    ):
        default_config = TranspileConfig(
            transpiler_config_path=str(transpiler_config_path),
            source_dialect="snowflake",
            input_source="/path/to/sql/file.sql",
            output_folder="/path/to/output",
            error_file_path="/path/to/errors.log",
            sdk_config=None,
            skip_validation=True,
            catalog_name="my_catalog",
            schema_name="my_schema",
        )
        mock_app_context.return_value.workspace_client = mock_workspace_client
        mock_app_context.return_value.prompts = prompts

        def set_default_config(config: TranspileConfig):
            mock_app_context.return_value.transpile_config = config

        set_default_config(default_config)
        yield mock_workspace_client, default_config, set_default_config, mock_transpile
        set_default_config(default_config)


def test_transpile_with_no_sdk_config(mock_cli_for_transpile, transpiler_config_path: Path) -> None:
    ws, cfg, set_cfg, do_transpile = mock_cli_for_transpile
    set_cfg(dataclasses.replace(cfg, sdk_config=None))
    cli.transpile(
        ws,
    )
    do_transpile.assert_called_once_with(
        ws,
        ANY,
        TranspileConfig(
            transpiler_config_path=str(transpiler_config_path),
            source_dialect="snowflake",
            input_source="/path/to/sql/file.sql",
            output_folder="/path/to/output",
            error_file_path="/path/to/errors.log",
            sdk_config=None,
            skip_validation=True,
            catalog_name="my_catalog",
            schema_name="my_schema",
            transpiler_options={'-experimental': False},
        ),
    )


def test_transpile_with_warehouse_id_in_sdk_config(mock_cli_for_transpile, transpiler_config_path: Path) -> None:
    ws, cfg, set_cfg, do_transpile = mock_cli_for_transpile
    sdk_config = {"warehouse_id": "w_id"}
    set_cfg(dataclasses.replace(cfg, sdk_config=sdk_config))
    cli.transpile(
        ws,
    )
    do_transpile.assert_called_once_with(
        ws,
        ANY,
        TranspileConfig(
            transpiler_config_path=str(transpiler_config_path),
            source_dialect="snowflake",
            input_source="/path/to/sql/file.sql",
            output_folder="/path/to/output",
            error_file_path="/path/to/errors.log",
            sdk_config=sdk_config,
            skip_validation=True,
            catalog_name="my_catalog",
            schema_name="my_schema",
            transpiler_options={'-experimental': False},
        ),
    )


def test_transpile_with_cluster_id_in_sdk_config(mock_cli_for_transpile, transpiler_config_path: Path) -> None:
    ws, cfg, set_cfg, do_transpile = mock_cli_for_transpile
    sdk_config = {"cluster_id": "c_id"}
    set_cfg(dataclasses.replace(cfg, sdk_config=sdk_config))
    cli.transpile(
        ws,
    )
    do_transpile.assert_called_once_with(
        ws,
        ANY,
        TranspileConfig(
            transpiler_config_path=str(transpiler_config_path),
            source_dialect="snowflake",
            input_source="/path/to/sql/file.sql",
            output_folder="/path/to/output",
            error_file_path="/path/to/errors.log",
            sdk_config=sdk_config,
            skip_validation=True,
            catalog_name="my_catalog",
            schema_name="my_schema",
            transpiler_options={'-experimental': False},
        ),
    )


def test_transpile_with_invalid_transpiler_config_path(mock_cli_for_transpile) -> None:
    ws, _cfg, _set_cfg, do_transpile = mock_cli_for_transpile
    cli.transpile(
        ws,
        transpiler_config_path="invalid_path",
    )
    do_transpile.assert_called_once_with(
        ws,
        ANY,
        TranspileConfig(
            transpiler_config_path=str(TRANSPILERS_PATH / "morpheus" / "lib" / "config.yml"),
            source_dialect="snowflake",
            input_source="/path/to/sql/file.sql",
            output_folder="/path/to/output",
            error_file_path="/path/to/errors.log",
            sdk_config=None,
            skip_validation=True,
            catalog_name="my_catalog",
            schema_name="my_schema",
            transpiler_options=None,
        ),
    )


def test_transpile_with_invalid_transpiler_dialect(mock_cli_for_transpile, transpiler_config_path: Path) -> None:
    ws, _cfg, _set_cfg, do_transpile = mock_cli_for_transpile
    with pytest.raises(ValueError):
        cli.transpile(
            ws,
            source_dialect="invalid_dialect",
        )
        do_transpile.assert_called_once_with(
            ws,
            ANY,
            TranspileConfig(
                transpiler_config_path=str(transpiler_config_path),
                source_dialect="snowflake",
                input_source="/path/to/sql/file.sql",
                output_folder="/path/to/output",
                error_file_path="/path/to/errors.log",
                sdk_config=None,
                skip_validation=True,
                catalog_name="my_catalog",
                schema_name="my_schema",
                transpiler_options={"-experimental": False},
            ),
        )


def test_transpile_with_invalid_skip_validation(mock_cli_for_transpile) -> None:
    ws, _cfg, _set_cfg, _do_transpile = mock_cli_for_transpile
    with (pytest.raises(Exception, match="Invalid value for '--skip-validation'"),):
        cli.transpile(
            ws,
            skip_validation="invalid_value",
        )


def test_transpile_with_invalid_input_source(mock_cli_for_transpile) -> None:
    ws, _cfg, _set_cfg, _do_transpile = mock_cli_for_transpile
    with (pytest.raises(Exception, match="Invalid value for '--input-source'"),):
        cli.transpile(
            ws,
            input_source="invalid_path",
        )


def test_transpile_with_valid_inputs(mock_cli_for_transpile, transpiler_config_path: Path) -> None:
    ws, _cfg, _set_cfg, do_transpile = mock_cli_for_transpile
    transpiler = str(transpiler_config_path)
    source_dialect = "oracle"
    input_source = "/other/path/to/sql/file.sql"
    output_folder = "/other/path/to/output"
    error_file = "/other/path/to/errors.log"
    skip_validation = "false"
    catalog_name = "other_catalog"
    schema_name = "other_schema"
    cli.transpile(
        ws,
        transpiler,
        source_dialect,
        input_source,
        output_folder,
        error_file,
        skip_validation,
        catalog_name,
        schema_name,
    )
    do_transpile.assert_called_once_with(
        ws,
        ANY,
        TranspileConfig(
            transpiler_config_path=str(TRANSPILERS_PATH / "rct" / "lib" / "config.yml"),
            source_dialect=source_dialect,
            input_source=input_source,
            output_folder=output_folder,
            error_file_path=error_file,
            sdk_config=None,
            skip_validation=False,
            catalog_name=catalog_name,
            schema_name=schema_name,
            transpiler_options={"-experimental": False},
        ),
    )


def test_transpile_with_real_inputs(mock_cli_for_transpile, transpiler_config_path: Path) -> None:
    ws, _cfg, _set_cfg, do_transpile = mock_cli_for_transpile
    with patch("os.path.exists", os.path.exists):
        source_dialect = "snowflake"
        input_source = path_to_resource("functional", "snowflake", "aggregates", "least_1.sql")
        output_folder = path_to_resource("lsp_transpiler")
        error_file_path = path_to_resource("lsp_transpiler", "error.log")
        skip_validation = "true"
        catalog_name = "my_catalog"
        schema_name = "my_schema"
        cli.transpile(
            ws,
            transpiler_config_path=str(transpiler_config_path),
            source_dialect=source_dialect,
            input_source=input_source,
            output_folder=output_folder,
            error_file_path=error_file_path,
            skip_validation=skip_validation,
            catalog_name=catalog_name,
            schema_name=schema_name,
        )
        do_transpile.assert_called_once_with(
            ws,
            ANY,
            TranspileConfig(
                transpiler_config_path=str(transpiler_config_path),
                source_dialect=source_dialect,
                input_source=input_source,
                output_folder=output_folder,
                error_file_path=error_file_path,
                sdk_config=None,
                skip_validation=True,
                catalog_name=catalog_name,
                schema_name=schema_name,
                transpiler_options={"-experimental": False},
            ),
        )


def test_transpile_prints_errors(caplog, tmp_path: Path, mock_workspace_client: WorkspaceClient) -> None:
    transpiler_config_path = path_to_resource("lsp_transpiler", "lsp_config.yml")
    source_dialect = "snowflake"
    input_source = path_to_resource("lsp_transpiler", "unsupported_lca.sql")
    output_folder = str(tmp_path)
    skip_validation = "true"
    catalog_name = "my_catalog"
    schema_name = "my_schema"
    error_file_path = "errors.log"
    prompts = MockPrompts(
        {
            "Do you want to use the experimental.*": "no",
        }
    )
    with (
        caplog.at_level("ERROR"),
        patch(
            "databricks.labs.lakebridge.contexts.application.ApplicationContext.prompts", new_callable=lambda: prompts
        ),
        patch("databricks.labs.lakebridge.install.TranspilerInstaller.all_dialects", return_value=[source_dialect]),
    ):
        cli.transpile(
            mock_workspace_client,
            transpiler_config_path=transpiler_config_path,
            source_dialect=source_dialect,
            input_source=input_source,
            output_folder=output_folder,
            skip_validation=skip_validation,
            catalog_name=catalog_name,
            schema_name=schema_name,
            error_file_path=error_file_path,
        )

    assert any(str(input_source) in record.message for record in caplog.records)
