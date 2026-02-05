from pathlib import Path
from unittest.mock import Mock

from databricks.labs.blueprint.tui import MockPrompts

from databricks.labs.lakebridge.analyzer.lakebridge_analyzer import LakebridgeAnalyzer, AnalyzerPrompts, AnalyzerRunner

from databricks.labs.bladespector.analyzer import Analyzer


def test_analyze_arguments_return(tmp_path: Path):
    mock_prompts = MockPrompts({})
    input_path = tmp_path / "in"
    report_file = tmp_path / "report.xlsx"
    tech = "Synapse"
    runner = AnalyzerRunner(runnable=Mock(), is_debug=True)
    analyzer = LakebridgeAnalyzer(AnalyzerPrompts(mock_prompts), runner)

    result = analyzer.run_analyzer(str(input_path), str(report_file), tech)

    assert result.source_directory == input_path
    assert result.report_path == report_file
    assert result.source_system == tech


def test_analyze_prompts_result(tmp_path: Path):
    supported_tech = sorted(Analyzer.supported_source_technologies(), key=str.casefold)
    tech_enum = next((i for i, tech in enumerate(supported_tech) if tech == "Informatica - PC"), 12)
    input_path = tmp_path / "in"
    output_path = tmp_path / "out"
    mock_prompts = MockPrompts(
        {
            "Select the source technology": str(tech_enum),
            "Enter the path of the directory containing sources to analyze": str(input_path),
            "Enter the path of the report file for analyzer results": str(output_path),
        }
    )
    runner = AnalyzerRunner(runnable=Mock(), is_debug=True)
    analyzer = LakebridgeAnalyzer(AnalyzerPrompts(mock_prompts), runner)

    result = analyzer.run_analyzer()

    assert result.source_directory == input_path
    assert result.report_path == output_path
    assert result.source_system == "Informatica - PC"
