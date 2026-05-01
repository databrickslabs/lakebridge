import json
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
    _directory: Path, result: Path, _platform: str, _is_debug: bool = False, json_result: Path | None = None
) -> None:
    """Stand in for Bladespector: create Excel path; when JSON is requested, create that file too."""
    result.touch()
    if json_result is not None:
        json_result.write_text(json.dumps({"mock": True}), encoding="utf-8")


@pytest.mark.parametrize(
    "report_file",
    (
        Path("report.xlsx"),
        Path("report-without-extension"),
    ),
    ids=str,
)
@pytest.mark.parametrize(
    "omit_generate_json_kw, expect_json_file",
    (
        (True, True),  # default: omit kwarg → JSON on
        (False, False),  # explicit generate_json=False
    ),
    ids=("default_json", "skip_json"),
)
def test_analyze_json_output(
    tmp_path: Path,
    report_file: Path,
    omit_generate_json_kw: bool,
    expect_json_file: bool,
) -> None:
    path = tmp_path / "in"
    file = tmp_path / report_file
    mock_prompts = MockPrompts({})

    runner = AnalyzerRunner(runnable=_mock_analyze, is_debug=True)
    expected_result = AnalyzerResult(source_directory=path, report_path=file, source_system=str("Synapse"))

    analyzer = LakebridgeAnalyzer(AnalyzerPrompts(mock_prompts), runner)
    if omit_generate_json_kw:
        result = analyzer.run_analyzer(source=str(path), report_file=str(file), platform="Synapse")
    else:
        result = analyzer.run_analyzer(
            source=str(path), report_file=str(file), platform="Synapse", generate_json=False
        )

    assert result == expected_result
    assert file.exists()
    json_path = file.with_suffix(".json")
    if expect_json_file:
        assert json_path.exists(), "JSON should be produced by default alongside Excel"
    else:
        assert not json_path.exists(), "JSON should not be produced when generate_json is False"


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


def test_analyze_prompts_skip_json(tmp_path: Path) -> None:
    first_tech = next(iter(sorted(Analyzer.supported_source_technologies(), key=str.casefold)))
    input_path = tmp_path / "in"
    report_file = tmp_path / "report-no-json.xlsx"
    mock_prompts = MockPrompts(
        {
            "Select the source technology": "0",
            "Enter the path of the directory containing sources to analyze": str(input_path),
            "Enter the path of the report file for analyzer results": str(report_file),
        }
    )
    expected_result = AnalyzerResult(source_directory=input_path, report_path=report_file, source_system=first_tech)
    _test_analyze_prompt(mock_prompts, expected_result, generate_json=False)


def _test_analyze_prompt(
    mock_prompts: MockPrompts, expected_result: AnalyzerResult, *, generate_json: bool | None = None
) -> None:
    runner = AnalyzerRunner(runnable=_mock_analyze, is_debug=True)
    analyzer = LakebridgeAnalyzer(AnalyzerPrompts(mock_prompts), runner)

    if generate_json is None:
        result = analyzer.run_analyzer()
    else:
        result = analyzer.run_analyzer(generate_json=generate_json)
    assert result == expected_result
    json_path = expected_result.report_path.with_suffix(".json")
    if generate_json is False:
        assert not json_path.exists(), "JSON should not be produced when generate_json is False"
    else:
        assert json_path.exists(), "JSON should be produced by default alongside Excel"
