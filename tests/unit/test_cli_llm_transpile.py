import logging
from pathlib import Path
from unittest.mock import create_autospec
from typing import cast

import pytest

from databricks.labs.blueprint.installation import MockInstallation, RootJsonValue
from databricks.labs.blueprint.tui import MockPrompts, Prompts
from databricks.labs.switch.lsp import get_switch_dialects

from databricks.labs.lakebridge import cli
from databricks.labs.lakebridge.contexts.application import ApplicationContext
from databricks.sdk import WorkspaceClient
from databricks.sdk.service.serving import ServingEndpoint, EndpointCoreConfigSummary, ServedEntitySpec, FoundationModel

from databricks.labs.lakebridge.deployment.configurator import ResourceConfigurator
from databricks.labs.lakebridge.helpers.metastore import CatalogOperations

_JOB_ID = 1234567890
_RUN_ID = 123456789
_switch_dialects = get_switch_dialects()


def make_mock_prompts(input_path: str, output_folder: str, source_dialect: str = "mssql") -> MockPrompts:
    return MockPrompts(
        {
            r"Enter input SQL path": str(input_path),
            r"Enter output workspace folder must start with /Workspace/": output_folder,
            r"Select the source dialect": str(sorted(_switch_dialects).index(source_dialect)),
            r"Enter catalog name": "lakebridge",
            r"Enter schema name": "switch",
            r"Enter volume name": "switch_volume",
            r"Select a Foundation Model serving endpoint:": "1",
        }
    )


def create_switch_workspace_client_mock() -> WorkspaceClient:
    ws = create_autospec(spec=WorkspaceClient, instance=True)

    ws.config.host = 'https://workspace.databricks.com'
    ws.files.upload.return_value = None
    ws.jobs.run_now.return_value.run_id = _RUN_ID
    ws.jobs.run_now_and_wait_result.return_value.run_id = _RUN_ID
    ws.serving_endpoints.list.return_value = [
        ServingEndpoint(
            name="databricks-claude-sonnet-4",
            config=EndpointCoreConfigSummary(
                served_entities=[
                    ServedEntitySpec(
                        foundation_model=FoundationModel(name="claude-sonnet-4"),
                    )
                ]
            ),
        ),
        ServingEndpoint(
            name="databricks-gpt-4-mini",
            config=EndpointCoreConfigSummary(
                served_entities=[
                    ServedEntitySpec(
                        foundation_model=FoundationModel(name="gpt-4-mini"),
                    )
                ]
            ),
        ),
    ]

    return ws


def mock_resource_configurator(ws: WorkspaceClient, prompts: Prompts) -> ResourceConfigurator:
    catalog_operations = create_autospec(CatalogOperations)
    catalog_operations.has_catalog_access.return_value = True
    return ResourceConfigurator(ws, prompts, catalog_operations)


@pytest.fixture
def mock_installation_with_switch() -> MockInstallation:
    """MockInstallation with Switch configuration state."""
    state: dict[str, RootJsonValue] = {
        "state.json": {"resources": {"jobs": {"Switch": f"{_JOB_ID}"}}, "version": 1},
    }
    return MockInstallation(cast(dict[str, RootJsonValue], state))


def test_llm_transpile_success(
    mock_installation_with_switch: MockInstallation,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Test successful LLM transpile execution."""
    input_source = tmp_path / "input.sql"
    input_source.write_text("SELECT * FROM table1;")
    output_folder = "/Workspace/Users/test/output"

    # Use a dedicated WorkspaceClient mock tailored for SwitchRunner
    mock_ws = create_switch_workspace_client_mock()
    mock_configurator = mock_resource_configurator(mock_ws, make_mock_prompts(str(input_source), output_folder))

    ctx = ApplicationContext(mock_ws)
    ctx.replace(
        installation=mock_installation_with_switch,
        add_user_agent_extra=lambda w, *args, **kwargs: w,
        resource_configurator=mock_configurator,
    )

    with caplog.at_level(logging.INFO):
        cli.llm_transpile(
            w=mock_ws,
            accept_terms=True,
            input_source=str(input_source),
            output_ws_folder=output_folder,
            source_dialect="mssql",
            catalog_name="lakebridge",
            schema_name="switch",
            volume="switch_volume",
            foundation_model="databricks-claude-sonnet-4-5",
            ctx=ctx,
        )

    expected_msg = (
        f"Switch LLM transpilation job started: https://workspace.databricks.com/jobs/{_JOB_ID}/runs/{_RUN_ID}"
    )
    info_messages = [record.message for record in caplog.records if record.levelno == logging.INFO]
    assert expected_msg in info_messages


def test_llm_transpile_terms_notice(
    mock_installation_with_switch: MockInstallation,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Verify that llm-transpile exits with just the terms notice when terms are not accepted."""
    mock_ws = create_autospec(spec=WorkspaceClient, instance=True)
    mock_prompts = MockPrompts({})
    mock_configurator = ResourceConfigurator(mock_ws, mock_prompts, create_autospec(CatalogOperations, instance=True))
    ctx = ApplicationContext(mock_ws).replace(
        installation=mock_installation_with_switch,
        prompts=mock_prompts,
        resource_configurator=mock_configurator,
    )

    with caplog.at_level(logging.WARNING), pytest.raises(SystemExit):
        cli.llm_transpile(w=mock_ws, ctx=ctx)

    # Verify that the terms notice was logged.
    warning_messages = [record.message for record in caplog.records if record.levelno == logging.WARNING]
    assert any(
        "By using this feature you accept these terms, re-run with '--accept-terms=true'." in msg
        for msg in warning_messages
    )

    # Verify that we didn't try to run the job.
    mock_ws.jobs.run_now.assert_not_called()
    mock_ws.jobs.run_now_and_wait.assert_not_called()


def test_llm_transpile_without_parms(
    mock_installation_with_switch: MockInstallation,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Test LLM transpile execution without parameters, relying on config file."""
    input_source = tmp_path / "input.sql"
    input_source.write_text("SELECT * FROM table1;")
    output_folder = "/Workspace/Users/test/output"

    mock_prompts = make_mock_prompts(str(input_source), output_folder, "mssql")

    # Use a dedicated WorkspaceClient mock tailored for SwitchRunner
    mock_ws = create_switch_workspace_client_mock()
    mock_configurator = mock_resource_configurator(mock_ws, make_mock_prompts(str(input_source), output_folder))

    ctx = ApplicationContext(mock_ws)

    ctx.replace(
        installation=mock_installation_with_switch,
        add_user_agent_extra=lambda w, *args, **kwargs: w,
        prompts=mock_prompts,
        resource_configurator=mock_configurator,
    )

    with caplog.at_level(logging.INFO):
        cli.llm_transpile(w=mock_ws, accept_terms=True, ctx=ctx)

    expected_msg = (
        f"Switch LLM transpilation job started: https://workspace.databricks.com/jobs/{_JOB_ID}/runs/{_RUN_ID}"
    )
    info_messages = [record.message for record in caplog.records if record.levelno == logging.INFO]
    assert expected_msg in info_messages


def test_llm_transpile_with_incorrect_output_parms(
    mock_installation_with_switch: MockInstallation,
    tmp_path: Path,
) -> None:

    input_source = tmp_path / "input.sql"
    input_source.write_text("SELECT * FROM table1;")
    output_folder = "/Users/test/output"

    mock_prompts = make_mock_prompts(str(input_source), output_folder, "mssql")

    # Use a dedicated WorkspaceClient mock tailored for SwitchRunner
    mock_ws = create_switch_workspace_client_mock()
    mock_configurator = mock_resource_configurator(mock_ws, make_mock_prompts(str(input_source), output_folder))

    ctx = ApplicationContext(mock_ws)

    ctx.replace(
        installation=mock_installation_with_switch,
        add_user_agent_extra=lambda w, *args, **kwargs: w,
        prompts=mock_prompts,
        resource_configurator=mock_configurator,
    )

    error_msg = "Invalid value for '--output-ws-folder': workspace output path must start with /Workspace/. Got: '/Users/test/output'"
    with pytest.raises(ValueError, match=rf"{error_msg}"):
        cli.llm_transpile(w=mock_ws, accept_terms=True, output_ws_folder=output_folder, ctx=ctx)


def test_llm_transpile_with_incorrect_dialect(
    mock_installation_with_switch: MockInstallation,
    tmp_path: Path,
) -> None:

    input_source = tmp_path / "input.sql"
    input_source.write_text("SELECT * FROM table1;")
    output_folder = "/Workspace/Users/test/output"

    # passing this but will override below to mimic incorrect dialect scenario
    mock_prompts = make_mock_prompts(str(input_source), output_folder, "mssql")

    # Use a dedicated WorkspaceClient mock tailored for SwitchRunner
    mock_ws = create_switch_workspace_client_mock()
    mock_configurator = mock_resource_configurator(mock_ws, make_mock_prompts(str(input_source), output_folder))

    ctx = ApplicationContext(mock_ws)

    ctx.replace(
        installation=mock_installation_with_switch,
        add_user_agent_extra=lambda w, *args, **kwargs: w,
        prompts=mock_prompts,
        resource_configurator=mock_configurator,
    )

    error_msg = "Invalid value for '--source-dialect': 'agent_sql' must be one of: airflow, mssql, mysql, netezza, oracle, postgresql, redshift, snowflake, synapse, teradata"
    with pytest.raises(ValueError, match=rf"{error_msg}"):
        cli.llm_transpile(w=mock_ws, accept_terms=True, source_dialect="agent_sql", ctx=ctx)
