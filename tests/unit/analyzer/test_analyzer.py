from pathlib import Path
from unittest.mock import Mock

from databricks.labs.bladespector.analyzer import Analyzer
from databricks.labs.blueprint.tui import MockPrompts

from databricks.labs.lakebridge.analyzer.lakebridge_analyzer import (
    AnalyzerPrompts,
    AnalyzerResult,
    AnalyzerRunner,
    LakebridgeAnalyzer,
)
from databricks.labs.lakebridge.helpers.file_utils import chdir


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
    first_tech = next(iter(sorted(Analyzer.supported_source_technologies(), key=str.casefold)))
    input_path = tmp_path / "in"
    report_file = tmp_path / "report.xlsx"
    mock_prompts = MockPrompts(
        {
            "Select the source technology": "0",
            "Enter the path of the directory containing sources to analyze": str(input_path),
            "Enter the path of the report file for analyzer results": str(report_file),
        }
    )
    expected_result = AnalyzerResult(source_directory=input_path, report_path=report_file, source_system=first_tech)
    _test_analyze_prompt(mock_prompts, expected_result)


def test_analyze_prompt_relative_result_path(tmp_path: Path) -> None:
    """Verify the handling when a relative path is provided for the report file."""
    first_tech = next(iter(sorted(Analyzer.supported_source_technologies(), key=str.casefold)))
    input_path = Path("in")
    report_file = Path("report.xlsx")
    mock_prompts = MockPrompts(
        {
            "Select the source technology": "0",
            "Enter the path of the directory containing sources to analyze": str(input_path),
            "Enter the path of the report file for analyzer results": str(report_file),
        }
    )
    expected_result = AnalyzerResult(
        source_directory=tmp_path / input_path, report_path=tmp_path / report_file, source_system=first_tech
    )

    with chdir(tmp_path):
        _test_analyze_prompt(mock_prompts, expected_result)


def _test_analyze_prompt(mock_prompts: MockPrompts, expected_result: AnalyzerResult) -> None:
    runner = AnalyzerRunner(runnable=Mock(), is_debug=True)
    analyzer = LakebridgeAnalyzer(AnalyzerPrompts(mock_prompts), runner)

    result = analyzer.run_analyzer()
    assert result == expected_result
