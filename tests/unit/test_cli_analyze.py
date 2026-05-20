import json
from pathlib import Path
from unittest.mock import patch

import pytest
from databricks.labs.blueprint.tui import MockPrompts
from databricks.sdk import WorkspaceClient

from databricks.labs.lakebridge import cli
from databricks.labs.lakebridge.contexts.application import ApplicationContext

from databricks.labs.bladespector.analyzer import Analyzer

# TODO: These should be moved to the integration tests.


@pytest.mark.parametrize(
    "report_file",
    (
        Path("sample.xlsx"),
        Path("file-without-extension"),
    ),
    ids=str,
)
@pytest.mark.parametrize("target_exists", (False, True), ids=lambda x: "target_exists" if x else "new_file")
def test_analyze_arguments(
    mock_workspace_client: WorkspaceClient,
    test_resources: Path,
    tmp_path: Path,
    report_file: Path,
    target_exists: bool,
) -> None:
    input_path = test_resources / "functional" / "informatica"
    report_path = tmp_path / report_file
    if target_exists:
        report_path.write_text("Target will already exist; needs to be overwritten.", encoding="utf-8")
    cli.analyze(
        w=mock_workspace_client,
        source_directory=str(input_path),
        report_file=str(report_path),
        source_tech="Informatica - PC",
    )

    with report_path.open("rb") as f:
        header = f.read(4)
    # Excel files are .zip files, so we can check they have the zip header.
    assert header == b"PK\x03\x04"


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


def test_analyze_generate_json(
    mock_workspace_client: WorkspaceClient,
    test_resources: Path,
    tmp_path: Path,
) -> None:
    input_path = test_resources / "functional" / "snowflake" / "integration"
    report_path = tmp_path / "report.xlsx"
    expected_json = tmp_path / "report.json"

    cli.analyze(
        w=mock_workspace_client,
        source_directory=str(input_path),
        report_file=str(report_path),
        source_tech="Snowflake",
        generate_json=True,
    )

    assert report_path.exists(), "Excel report was not created"
    assert expected_json.exists(), "JSON report was not created"
    with expected_json.open("r", encoding="utf-8") as f:
        data = json.load(f)
    assert isinstance(data, dict), "JSON report is not a valid JSON object"


def test_analyze_prompts(mock_workspace_client: WorkspaceClient, test_resources: Path, tmp_path: Path) -> None:

    supported_tech = sorted(Analyzer.supported_source_technologies(), key=str.casefold)
    tech_enum = next((i for i, tech in enumerate(supported_tech) if tech == "Informatica - PC"), 12)

    source_dir = test_resources / "functional" / "informatica"
    report_path = tmp_path / "results.xlsx"

    mock_prompts = MockPrompts(
        {
            "Select the source technology": str(tech_enum),
            "Enter the path of the directory containing sources to analyze": str(source_dir),
            "Enter the path of the report file for analyzer results": str(report_path),
        }
    )
    with patch.object(ApplicationContext, "prompts", mock_prompts):
        cli.analyze(w=mock_workspace_client)
