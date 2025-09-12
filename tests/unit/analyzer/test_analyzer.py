from pathlib import Path
from unittest.mock import patch

from databricks.labs.blueprint.tui import MockPrompts

from databricks.labs.lakebridge.analyzer.lakebridge_analyzer import LakebridgeAnalyzer

from databricks.labs.bladespector.analyzer import Analyzer


def test_analyze_arguments_return(tmp_path: Path):
    mock_prompts = MockPrompts({})
    input_path = tmp_path / "in"
    output_path = tmp_path / "out"
    tech = "Synapse"
    analyzer = LakebridgeAnalyzer(mock_prompts, True)

    with (
        patch.object(analyzer, "_run_binary", return_value=None),
        patch("databricks.labs.lakebridge.analyzer.lakebridge_analyzer.move_tmp_file", return_value=None),
    ):
        result = analyzer.run_analyzer(str(input_path), str(output_path), tech)

        assert result.source_directory == input_path
        assert result.output_directory == output_path
        assert result.source_system == tech


def test_analyze_prompts_result(tmp_path: Path):
    supported_tech = sorted(Analyzer.supported_source_technologies(), key=str.casefold)
    tech_enum = next((i for i, tech in enumerate(supported_tech) if tech == "Informatica - PC"), 12)
    input_path = tmp_path / "in"
    output_path = tmp_path / "out"
    mock_prompts = MockPrompts(
        {
            "Select the source technology": str(tech_enum),
            "Enter full path to the source directory": str(input_path),
            "Enter report file name or custom export path including file name without extension": str(output_path),
        }
    )
    analyzer = LakebridgeAnalyzer(mock_prompts, True)

    with (
        patch.object(analyzer, "_run_binary", return_value=None),
        patch("databricks.labs.lakebridge.analyzer.lakebridge_analyzer.move_tmp_file", return_value=None),
    ):
        result = analyzer.run_analyzer()

        assert result.source_directory == input_path
        assert result.output_directory == output_path
        assert result.source_system == "Informatica - PC"
