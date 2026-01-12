from pathlib import Path
from unittest.mock import patch

from databricks.labs.blueprint.tui import MockPrompts
from databricks.sdk import WorkspaceClient

from databricks.labs.lakebridge import cli
from databricks.labs.lakebridge.contexts.application import ApplicationContext

from databricks.labs.bladespector.analyzer import Analyzer

# TODO: These should be moved to the integration tests.


def test_analyze_arguments(mock_workspace_client: WorkspaceClient, test_resources: Path, tmp_path: Path) -> None:
    input_path = test_resources / "functional" / "informatica"
    cli.analyze(
        w=mock_workspace_client,
        source_directory=str(input_path),
        report_file=str(tmp_path / "sample"),
        source_tech="Informatica - PC",
    )


def test_analyze_arguments_wrong_tech(
    mock_workspace_client: WorkspaceClient,
    test_resources: Path,
    tmp_path: Path,
) -> None:

    supported_tech = sorted(Analyzer.supported_source_technologies(), key=str.casefold)
    tech_enum = next((i for i, tech in enumerate(supported_tech) if tech == "Informatica - PC"), 12)

    mock_prompts = MockPrompts(
        {
            "Select the source technology": str(tech_enum),
        }
    )

    with patch.object(ApplicationContext, "prompts", mock_prompts):
        input_path = test_resources / "functional" / "informatica"
        cli.analyze(
            w=mock_workspace_client,
            source_directory=str(input_path),
            report_file=str(tmp_path / "sample.xlsx"),
            source_tech="Informatica",
        )


def test_analyze_prompts(mock_workspace_client: WorkspaceClient, test_resources: Path, tmp_path: Path) -> None:

    supported_tech = sorted(Analyzer.supported_source_technologies(), key=str.casefold)
    tech_enum = next((i for i, tech in enumerate(supported_tech) if tech == "Informatica - PC"), 12)

    source_dir = test_resources / "functional" / "informatica"
    output_dir = tmp_path / "results"

    mock_prompts = MockPrompts(
        {
            "Select the source technology": str(tech_enum),
            "Enter full path to the source directory": str(source_dir),
            "Enter report file name or custom export path including file name without extension": str(output_dir),
        }
    )
    with patch.object(ApplicationContext, "prompts", mock_prompts):
        cli.analyze(w=mock_workspace_client)
