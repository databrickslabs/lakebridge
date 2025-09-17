import dataclasses
import logging
import re
from collections.abc import Generator, Callable
from unittest.mock import create_autospec, patch, ANY, MagicMock
from pathlib import Path

import pytest
import yaml

from databricks.labs.lakebridge import cli

from databricks.labs.blueprint.tui import MockPrompts
from databricks.labs.lakebridge.config import TranspileConfig
from databricks.sdk import WorkspaceClient

from databricks.labs.lakebridge.contexts.application import ApplicationContext
from databricks.labs.lakebridge.transpiler.repository import TranspilerRepository
from tests.unit.conftest import path_to_resource

TRANSPILERS_PATH = Path(__file__).parent.parent / "resources" / "transpiler_configs"


@pytest.fixture()
def transpiler_repository(tmp_path: Path) -> TranspilerRepository:
    return TranspilerRepository(tmp_path)


@pytest.fixture(name="transpiler_config_path")
def stubbed_transpiler_config_path(tmp_path: Path) -> Path:
    """Path to a stubbed LSP transpiler configuration file."""
    # Refer to LSPConfig.load() for the expected structure; there is no .save() method.
    lsp_configuration = {
        "remorph": {
            "version": 1,
            "name": "Stubbed LSP Transpiler Configuration",
            "dialects": ["snowflake", "informatica pc"],
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

    config_path = tmp_path / "remorph-transpilers" / "stub-transpiler" / "lib" / "config.yml"
    config_path.parent.mkdir(parents=True)
    with config_path.open("w") as f:
        yaml.dump(lsp_configuration, f)
    return config_path


def test_transpile_with_missing_installation(
    caplog,
    transpiler_repository: TranspilerRepository,
    transpiler_config_path: Path,
    empty_input_source: Path,
    output_folder: Path,
) -> None:
    """Test that the CLI warns but continues when no workspace transpile configuration is found."""
    workspace_client = create_autospec(WorkspaceClient)
    mock_transpile = MagicMock(return_value=({}, []))

    async def patched_do_transpile(*args, **kwargs):
        return mock_transpile(*args, **kwargs)

    with (
        patch("databricks.labs.lakebridge.cli.ApplicationContext", autospec=True) as mock_app_context,
        patch("databricks.labs.lakebridge.cli.do_transpile", new=patched_do_transpile),
        caplog.at_level(logging.WARNING),
    ):
        mock_app_context.return_value.workspace_client = workspace_client
        mock_app_context.return_value.transpile_config = None
        cli.transpile(
            w=workspace_client,
            transpiler_config_path=str(transpiler_config_path),
            source_dialect="snowflake",
            input_source=str(empty_input_source),
            output_folder=str(output_folder),
            transpiler_repository=transpiler_repository,
        )

    mock_transpile.assert_called_once()
    warning_messages = [record.message for record in caplog.records if record.levelno == logging.WARNING]
    expected_msg = "No workspace transpile configuration, use 'install-transpile' to (re)install and configure; using defaults for now."
    assert expected_msg in warning_messages


@pytest.fixture
def mock_cli_for_transpile(
    mock_workspace_client: WorkspaceClient,
    transpiler_config_path: Path,
    empty_input_source: Path,
    output_folder: Path,
) -> Generator[tuple[WorkspaceClient, TranspileConfig, Callable[[TranspileConfig], None], MagicMock], None, None]:
    mock_transpile = MagicMock(return_value=({}, []))

    async def do_transpile(*args, **kwargs):
        return mock_transpile(*args, **kwargs)

    mock_app_context = create_autospec(ApplicationContext)
    with (
        patch("databricks.labs.lakebridge.cli.do_transpile", new=do_transpile),
        patch("databricks.labs.lakebridge.cli.ApplicationContext", mock_app_context),
    ):
        default_config = TranspileConfig(
            transpiler_config_path=str(transpiler_config_path),
            source_dialect="snowflake",
            input_source=str(empty_input_source),
            output_folder=str(output_folder),
            sdk_config=None,
            skip_validation=True,
            catalog_name="my_catalog",
            schema_name="my_schema",
        )
        mock_app_context.return_value.workspace_client = mock_workspace_client

        def set_default_config(config: TranspileConfig) -> None:
            mock_app_context.return_value.transpile_config = config

        set_default_config(default_config)
        yield mock_workspace_client, default_config, set_default_config, mock_transpile
        set_default_config(default_config)


@pytest.fixture
def mock_cli_transpile_no_config(
    mock_workspace_client: WorkspaceClient,
    transpiler_config_path: Path,
    empty_input_source: Path,
    output_folder: Path,
) -> Generator[tuple[WorkspaceClient, TranspileConfig, MagicMock], None, None]:
    mock_transpile = MagicMock(return_value=({}, []))

    async def do_transpile(*args, **kwargs):
        return mock_transpile(*args, **kwargs)

    prompts = MockPrompts(
        {
            "Do you want to use the experimental.*": "no",
            "Select the source dialect.*": "0",
            "Enter input SQL path.*": str(empty_input_source),
            "Enter output folder.*": str(output_folder),
        }
    )
    mock_app_context = create_autospec(ApplicationContext)
    with (
        patch("databricks.labs.lakebridge.cli.do_transpile", new=do_transpile),
        patch("databricks.labs.lakebridge.cli.ApplicationContext", mock_app_context),
    ):
        mock_app_context.return_value.workspace_client = mock_workspace_client
        mock_app_context.return_value.prompts = prompts
        mock_app_context.return_value.transpile_config = None  # No pre-set config

        expected_config = TranspileConfig(
            transpiler_config_path=str(transpiler_config_path),
            source_dialect="informatica pc",
            input_source=str(empty_input_source),
            output_folder=str(output_folder),
            sdk_config=None,
            skip_validation=False,
            catalog_name="remorph",
            schema_name="transpiler",
        )

        yield mock_workspace_client, expected_config, mock_transpile


def test_transpile_with_no_sdk_config(mock_cli_for_transpile) -> None:
    ws, cfg, set_cfg, do_transpile = mock_cli_for_transpile
    set_cfg(dataclasses.replace(cfg, sdk_config=None))
    cli.transpile(w=ws)
    do_transpile.assert_called_once_with(
        ws,
        ANY,
        TranspileConfig(
            transpiler_config_path=cfg.transpiler_config_path,
            source_dialect=cfg.source_dialect,
            input_source=cfg.input_source,
            output_folder=cfg.output_folder,
            error_file_path=cfg.error_file_path,
            sdk_config=None,
            skip_validation=cfg.skip_validation,
            catalog_name=cfg.catalog_name,
            schema_name=cfg.schema_name,
            transpiler_options=cfg.transpiler_options,
        ),
    )


def test_transpile_with_warehouse_id_in_sdk_config(
    mock_cli_for_transpile, transpiler_repository: TranspilerRepository
) -> None:
    ws, cfg, set_cfg, do_transpile = mock_cli_for_transpile
    sdk_config = {"warehouse_id": "w_id"}
    set_cfg(dataclasses.replace(cfg, sdk_config=sdk_config))
    cli.transpile(w=ws, transpiler_repository=transpiler_repository)
    do_transpile.assert_called_once_with(
        ws,
        ANY,
        TranspileConfig(
            transpiler_config_path=cfg.transpiler_config_path,
            source_dialect=cfg.source_dialect,
            input_source=cfg.input_source,
            output_folder=cfg.output_folder,
            sdk_config=sdk_config,
            skip_validation=cfg.skip_validation,
            catalog_name=cfg.catalog_name,
            schema_name=cfg.schema_name,
            transpiler_options=cfg.transpiler_options,
        ),
    )


def test_transpile_with_cluster_id_in_sdk_config(
    mock_cli_for_transpile, transpiler_repository: TranspilerRepository
) -> None:
    ws, cfg, set_cfg, do_transpile = mock_cli_for_transpile
    sdk_config = {"cluster_id": "c_id"}
    set_cfg(dataclasses.replace(cfg, sdk_config=sdk_config))
    cli.transpile(w=ws, transpiler_repository=transpiler_repository)
    do_transpile.assert_called_once_with(
        ws,
        ANY,
        TranspileConfig(
            transpiler_config_path=cfg.transpiler_config_path,
            source_dialect=cfg.source_dialect,
            input_source=cfg.input_source,
            output_folder=cfg.output_folder,
            sdk_config=sdk_config,
            skip_validation=cfg.skip_validation,
            catalog_name=cfg.catalog_name,
            schema_name=cfg.schema_name,
            transpiler_options=cfg.transpiler_options,
        ),
    )


def test_transpile_error_with_invalid_transpiler_config_path_override(
    mock_cli_for_transpile, transpiler_repository: TranspilerRepository
) -> None:
    ws, _, _, do_transpile = mock_cli_for_transpile
    expected_error = "Invalid path for '--transpiler-config-path', does not exist: invalid_path"
    with pytest.raises(ValueError, match=re.escape(expected_error)):
        cli.transpile(w=ws, transpiler_config_path="invalid_path", transpiler_repository=transpiler_repository)
    do_transpile.assert_not_called()


def test_transpile_error_with_invalid_transpiler_config_path_configuration(
    mock_cli_for_transpile, transpiler_repository: TranspilerRepository
) -> None:
    ws, cfg, set_cfg, do_transpile = mock_cli_for_transpile
    set_cfg(dataclasses.replace(cfg, transpiler_config_path="invalid_path"))
    expected_error = "Error: Invalid value for '--transpiler-config-path': 'invalid_path', file does not exist."
    with pytest.raises(ValueError, match=re.escape(expected_error)):
        cli.transpile(w=ws, transpiler_repository=transpiler_repository)
    do_transpile.assert_not_called()


def test_transpile_with_invalid_transpiler_dialect(
    mock_cli_for_transpile, transpiler_repository: TranspilerRepository
) -> None:
    ws, _, _, _ = mock_cli_for_transpile
    with pytest.raises(ValueError):
        cli.transpile(w=ws, source_dialect="invalid_dialect", transpiler_repository=transpiler_repository)


def test_transpile_with_invalid_skip_validation(
    mock_cli_for_transpile, transpiler_repository: TranspilerRepository
) -> None:
    ws, _, _, _ = mock_cli_for_transpile
    with pytest.raises(Exception, match="Invalid value for '--skip-validation'"):
        cli.transpile(w=ws, skip_validation="invalid_value", transpiler_repository=transpiler_repository)


def test_transpile_with_invalid_input_source(
    mock_cli_for_transpile, transpiler_repository: TranspilerRepository
) -> None:
    ws, _, _, _ = mock_cli_for_transpile
    msg = "Invalid path for '--input-source', does not exist: invalid_path"
    with pytest.raises(Exception, match=re.escape(msg)):
        cli.transpile(w=ws, input_source="invalid_path", transpiler_repository=transpiler_repository)


def test_transpile_with_valid_inputs(
    mock_cli_for_transpile, transpiler_config_path: Path, transpiler_repository: TranspilerRepository
) -> None:
    ws, cfg, _, do_transpile = mock_cli_for_transpile
    cli.transpile(
        w=ws,
        transpiler_config_path=cfg.transpiler_config_path,
        source_dialect=cfg.source_dialect,
        input_source=cfg.input_source,
        output_folder=cfg.output_folder,
        error_file_path=cfg.error_file_path,
        skip_validation=str(cfg.skip_validation),
        catalog_name=cfg.catalog_name,
        schema_name=cfg.schema_name,
        transpiler_repository=transpiler_repository,
    )
    do_transpile.assert_called_once_with(
        ws,
        ANY,
        TranspileConfig(
            transpiler_config_path=cfg.transpiler_config_path,
            source_dialect=cfg.source_dialect,
            input_source=cfg.input_source,
            output_folder=cfg.output_folder,
            error_file_path=cfg.error_file_path,
            sdk_config=cfg.sdk_config,
            skip_validation=cfg.skip_validation,
            catalog_name=cfg.catalog_name,
            schema_name=cfg.schema_name,
        ),
    )


def test_transpile_prints_errors(
    caplog, tmp_path: Path, mock_workspace_client: WorkspaceClient, transpiler_repository: TranspilerRepository
) -> None:
    input_source = path_to_resource("lsp_transpiler", "unsupported_lca.sql")
    with caplog.at_level("ERROR"):
        cli.transpile(
            w=mock_workspace_client,
            transpiler_config_path=path_to_resource("lsp_transpiler", "lsp_config.yml"),
            source_dialect="snowflake",
            input_source=input_source,
            output_folder=str(tmp_path),
            skip_validation="true",
            catalog_name="my_catalog",
            schema_name="my_schema",
            transpiler_repository=transpiler_repository,
        )

    assert any(str(input_source) in record.message for record in caplog.records)


def test_transpile_informatica_transpiler_dialect(
    mock_cli_for_transpile, transpiler_repository: TranspilerRepository
) -> None:
    ws, cfg, _, do_transpile = mock_cli_for_transpile
    # Test with Informatica PC dialect ensure user agent handles sources dialect with spaces in them
    cli.transpile(w=ws, source_dialect="informatica pc", transpiler_repository=transpiler_repository)
    do_transpile.assert_called_once_with(
        ws,
        ANY,
        TranspileConfig(
            transpiler_config_path=cfg.transpiler_config_path,
            source_dialect="informatica pc",
            input_source=cfg.input_source,
            output_folder=cfg.output_folder,
            error_file_path=cfg.error_file_path,
            sdk_config=cfg.sdk_config,
            skip_validation=cfg.skip_validation,
            catalog_name=cfg.catalog_name,
            schema_name=cfg.schema_name,
        ),
    )


def test_transpile_no_config(
    mock_cli_transpile_no_config, transpiler_repository: TranspilerRepository, transpiler_config_path: Path
) -> None:
    ws, expected_config, do_transpile = mock_cli_transpile_no_config
    cli.transpile(w=ws, transpiler_repository=transpiler_repository)
    do_transpile.assert_called_once_with(
        ws,
        ANY,
        expected_config,
    )


def test_transpile_no_config_with_source_override(
    mock_cli_transpile_no_config, transpiler_repository: TranspilerRepository, transpiler_config_path: Path
) -> None:
    ws, expected_config, do_transpile = mock_cli_transpile_no_config
    cli.transpile(w=ws, transpiler_repository=transpiler_repository, source_dialect="snowflake")
    expected_config = dataclasses.replace(expected_config, source_dialect="snowflake")
    do_transpile.assert_called_once_with(
        ws,
        ANY,
        expected_config,
    )
