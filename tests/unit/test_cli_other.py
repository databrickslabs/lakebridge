import io
from unittest.mock import Mock, patch

import pytest

from databricks.labs.blueprint.tui import MockPrompts
from databricks.labs.lakebridge import cli
from databricks.labs.lakebridge.config import LSPConfigOptionV1, LSPPromptMethod


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


def test_cli_reconcile(mock_workspace_client):
    with patch("databricks.labs.lakebridge.reconcile.runner.ReconcileRunner.run", return_value=True):
        cli.reconcile(w=mock_workspace_client)


def test_cli_aggregates_reconcile(mock_workspace_client):
    with patch("databricks.labs.lakebridge.reconcile.runner.ReconcileRunner.run", return_value=True):
        cli.aggregates_reconcile(w=mock_workspace_client)


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
