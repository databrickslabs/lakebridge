from pathlib import Path
from unittest.mock import create_autospec, patch

from databricks.labs.blueprint.tui import MockPrompts
from databricks.labs.lakebridge import cli
from databricks.sdk import WorkspaceClient

from databricks.labs.lakebridge.contexts.application import ApplicationContext


def test_analyze():
    # TODO: Currently this tests randomly relies on prompt number 5, that is SQL, 11 is INFA
    # we need to make it more stable by relying on dynamic mapping from source technology to prompt number
    prompts = MockPrompts(
        {
            r"Select the source technology": "11",
        }
    )
    with patch.object(ApplicationContext, "prompts", prompts):
        ws = create_autospec(WorkspaceClient)
        input_path = str(Path(__file__).parent.parent / "resources" / "functional" / "informatica")
        cli.analyze(ws, input_path, "/tmp/sample.xlsx")
