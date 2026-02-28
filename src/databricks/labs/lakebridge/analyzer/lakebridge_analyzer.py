import dataclasses
import shutil
import tempfile
from pathlib import Path
from collections.abc import Callable

from databricks.labs.blueprint.entrypoint import get_logger
from databricks.labs.blueprint.tui import Prompts

from databricks.labs.bladespector.analyzer import Analyzer

from databricks.labs.lakebridge.helpers.file_utils import check_path

logger = get_logger(__file__)


@dataclasses.dataclass
class AnalyzerResult:
    source_directory: Path
    report_path: Path
    source_system: str


class AnalyzerPrompts:

    def __init__(self, prompts: Prompts):
        self._prompts = prompts

    def get_source_directory(self) -> Path:
        """Prompt the user for the directory containing sources to analyze."""
        directory_str = self._prompts.question(
            "Enter the path of the directory containing sources to analyze",
            default=Path.cwd().as_posix(),
            validate=check_path,
        )
        return Path(directory_str)

    def get_result_file_path(self) -> Path:
        """Prompt the user for where the analyzer report should be saved."""
        filename = self._prompts.question(
            "Enter the path of the report file for analyzer results",
            default="lakebridge-analyzer-results.xlsx",
            validate=check_path,
        )
        return Path(filename)

    def get_source_system(self, platform: str | None = None) -> str:
        """Validate source technology or prompt for a valid source"""
        if platform is None or platform not in Analyzer.supported_source_technologies():
            if platform is not None:
                logger.warning(f"Invalid source technology {platform}")
            platform = self._prompts.choice("Select the source technology", Analyzer.supported_source_technologies())
        return platform


class AnalyzerRunner:
    def __init__(self, runnable: Callable[[Path, Path, str, bool, Path | None], None], is_debug: bool) -> None:
        self._runnable = runnable
        self._is_debug = is_debug

    @classmethod
    def create(cls, is_debug: bool = False) -> "AnalyzerRunner":
        return cls(Analyzer.analyze, is_debug)

    def run(
        self, source_dir: Path, results_file_path: Path, platform: str, generate_json: bool = False
    ) -> AnalyzerResult:
        logger.debug(f"Starting analyzer execution for {platform}: {source_dir}")

        if not source_dir.is_absolute():
            source_dir = source_dir.resolve()
            logger.debug(f"Relative path provided for source directory, will use: {source_dir}")
        if not results_file_path.is_absolute():
            results_file_path = results_file_path.resolve()
            logger.debug(f"Relative path provided for results file, will use: {results_file_path}")

        if not check_path(source_dir):
            raise ValueError(f"Invalid source directory, not writable: {source_dir}")
        if not check_path(results_file_path):
            raise ValueError(f"Invalid result path, not writable: {results_file_path}")

        json_result = results_file_path.with_suffix(".json") if generate_json else None

        _runnable: Callable[[Path, Path, str, bool, Path | None], None]
        if results_file_path.suffix == ".xlsx":
            _runnable = self._runnable
        else:
            # Bladespector currently fails if the path doesn't have a .xlsx extension.
            logger.warning(f"Excel report will be written without .xlsx extension: {results_file_path}")
            _runnable = self._run_with_staged_report
        _runnable(source_dir, results_file_path, platform, self._is_debug, json_result)
        logger.info(f"Analyzed {platform} files in {source_dir}; report saved to: {results_file_path}")
        return AnalyzerResult(source_dir, results_file_path, platform)

    def _run_with_staged_report(
        self,
        source_dir: Path,
        results_file_path: Path,
        platform: str,
        is_debug: bool,
        json_result: Path | None = None,
    ) -> None:
        """Run the analyzer, staging the results first to a temporary directory.

        This is a workaround: bladespector currently imposes restrictions on file names.
        """
        # TODO: Move this workaround to bladespector, so this can be eliminated here.
        with tempfile.TemporaryDirectory() as tmp_dir:
            staging_path = Path(tmp_dir) / "staging-report.xlsx"
            self._runnable(source_dir, staging_path, platform, is_debug, json_result)
            # On Windows, can't overwrite via move() so first need to remove the target if it exists.
            results_file_path.unlink(missing_ok=True)
            shutil.move(staging_path, results_file_path)
            logger.debug(f"Report moved from staging to requested location: {staging_path} -> {results_file_path}")


class LakebridgeAnalyzer:

    def __init__(self, prompts: AnalyzerPrompts, runner: AnalyzerRunner):
        self._prompts = prompts
        self._runner = runner

    def run_analyzer(
        self,
        source: str | None = None,
        report_file: str | None = None,
        platform: str | None = None,
        generate_json: bool = False,
    ) -> AnalyzerResult:
        source_dir = self._prompts.get_source_directory() if source is None else Path(source)
        results_file_path = self._prompts.get_result_file_path() if report_file is None else Path(report_file)
        platform = self._prompts.get_source_system(platform)

        return self._runner.run(source_dir, results_file_path, platform, generate_json)
