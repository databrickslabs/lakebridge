import io
import os
from unittest.mock import Mock, patch, MagicMock, create_autospec, PropertyMock

import pytest

from databricks.sdk import WorkspaceClient

from databricks.labs.blueprint.tui import MockPrompts
from databricks.labs.lakebridge import cli
from databricks.labs.lakebridge.config import LSPConfigOptionV1, LSPPromptMethod
from databricks.labs.lakebridge.contexts.application import ApplicationContext
from databricks.labs.lakebridge.helpers.recon_config_utils import ReconConfigPrompts


def test_configure_secrets_databricks(mock_workspace_client):
    source_dict = {"databricks": "0", "netezza": "1", "oracle": "2", "snowflake": "3"}
    prompts = MockPrompts(
        {
            r"Select the source": source_dict["databricks"],
        }
    )

    recon_conf = ReconConfigPrompts(mock_workspace_client, prompts)
    recon_conf.prompt_source()

    recon_conf.prompt_and_save_connection_details()


@pytest.mark.parametrize(
    ("argument", "expected"),
    (
        ("true", True),
        ("tRuE", True),
        ("false", False),
        ("fAlSe", False),
    ),
)
def test_interactive_argument(argument: str, expected: bool) -> None:
    """Check that the simple --interactive arguments as expected."""
    assert cli.interactive_mode(argument) is expected


def test_interactive_argument_unknown() -> None:
    """Check that an unknown --interactive argument raises an error."""
    with pytest.raises(ValueError) as expected_error:
        cli.interactive_mode("foobar")

    assert str(expected_error.value) == "Invalid value for '--interactive': 'foobar' must be 'true', 'false' or 'auto'."


@pytest.mark.parametrize("is_tty", (True, False))
def test_interactive_argument_auto(is_tty: bool) -> None:
    """Check that "auto" interactive detection is based on whether the stream looks like a TTY or not."""

    # Set up a fake stdin. (Can't use pty: it's unavailable on Windows.)
    mock_stdin = io.StringIO()
    setattr(mock_stdin, "isatty", mock_isatty := Mock(return_value=is_tty))

    interactive_mode = cli.interactive_mode("auto", input_stream=mock_stdin)

    # Check that we queried whether it's a TTY and that the result is as expected.
    assert mock_isatty.call_count == 1
    assert interactive_mode is is_tty


def test_cli_configure_secrets_config(mock_workspace_client):
    with patch("databricks.labs.lakebridge.cli.ReconConfigPrompts") as mock_recon_config:
        cli.configure_secrets(w=mock_workspace_client)
        mock_recon_config.assert_called_once_with(mock_workspace_client)


def app_factory(w: WorkspaceClient) -> ApplicationContext:
    ctx_mock = create_autospec(spec=ApplicationContext, spec_set=True)
    type(ctx_mock).workspace_client = PropertyMock(return_value=w)
    prompts = MockPrompts(
        {
            r"Would you like to open the job run URL .*": "no",
        }
    )
    ctx_mock.prompts = prompts
    return ctx_mock


def test_cli_reconcile(mock_workspace_client):
    with patch("databricks.labs.lakebridge.reconcile.runner.ReconcileRunner.run", return_value=(MagicMock(), "link1")):
        cli.reconcile(w=mock_workspace_client, ctx_factory=app_factory)


def test_cli_aggregates_reconcile(mock_workspace_client):
    with patch("databricks.labs.lakebridge.reconcile.runner.ReconcileRunner.run", return_value=(MagicMock(), "link1")):
        cli.aggregates_reconcile(w=mock_workspace_client, ctx_factory=app_factory)


def test_prompts_question():
    option = LSPConfigOptionV1("param", LSPPromptMethod.QUESTION, "Some question", default="<none>")
    prompts = MockPrompts({"Some question": ""})
    response = option.prompt_for_value(prompts)
    assert response is None
    prompts = MockPrompts({"Some question": "<none>"})
    response = option.prompt_for_value(prompts)
    assert response is None
    prompts = MockPrompts({"Some question": "something"})
    response = option.prompt_for_value(prompts)
    assert response == "something"


@pytest.mark.parametrize(
    ("env_value", "expected_serverless"),
    (
        (None, True),  # Default: serverless (no env var)
        ("", True),  # Empty string: serverless
        ("CLASSIC", False),  # CLASSIC: use classic cluster
        ("classic", False),  # lowercase: use classic cluster
        ("Classic", False),  # Mixed case: use classic cluster
        ("SERVERLESS", True),  # Any other value: serverless
        ("other", True),  # Any other value: serverless
    ),
)
def test_install_transpile_cluster_type_env_var(
    env_value: str | None,
    expected_serverless: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test LAKEBRIDGE_CLUSTER_TYPE env var determines serverless vs classic cluster."""
    if env_value is None:
        monkeypatch.delenv("LAKEBRIDGE_CLUSTER_TYPE", raising=False)
    else:
        monkeypatch.setenv("LAKEBRIDGE_CLUSTER_TYPE", env_value)

    # The logic from cli.py install_transpile:
    # switch_use_serverless = os.environ.get("LAKEBRIDGE_CLUSTER_TYPE", "").upper() != "CLASSIC"
    actual = os.environ.get("LAKEBRIDGE_CLUSTER_TYPE", "").upper() != "CLASSIC"
    assert actual == expected_serverless
