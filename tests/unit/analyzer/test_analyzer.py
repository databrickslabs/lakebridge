from pathlib import Path

import pytest
from databricks.labs.bladespector.analyzer import Analyzer
from databricks.labs.blueprint.tui import MockPrompts

from databricks.labs.lakebridge.analyzer.lakebridge_analyzer import (
    AnalyzerPrompts,
    AnalyzerResult,
    AnalyzerRunner,
    LakebridgeAnalyzer,
)
from databricks.labs.lakebridge.helpers.file_utils import chdir


def _mock_analyze(
    _directory: Path, result: Path, _platform: str, _is_debug: bool = False, _json_result: Path | None = None
) -> None:
    # Nothing really needed here, except a result needs to be created.
    result.touch()


@pytest.mark.parametrize(
    "report_file",
    (
        Path("report.xlsx"),
        Path("report-without-extension"),
    ),
    ids=str,
)
def test_analyze_arguments_return(tmp_path: Path, report_file: Path) -> None:
    path = tmp_path / "in"
    file = tmp_path / report_file
    mock_prompts = MockPrompts({})

    runner = AnalyzerRunner(runnable=_mock_analyze, is_debug=True)
    expected_result = AnalyzerResult(source_directory=path, report_path=file, source_system=str("Synapse"))

    analyzer = LakebridgeAnalyzer(AnalyzerPrompts(mock_prompts), runner)
    result = analyzer.run_analyzer(source=str(path), report_file=str(file), platform="Synapse")

    assert result == expected_result


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
    runner = AnalyzerRunner(runnable=_mock_analyze, is_debug=True)
    analyzer = LakebridgeAnalyzer(AnalyzerPrompts(mock_prompts), runner)

    result = analyzer.run_analyzer()
    assert result == expected_result
