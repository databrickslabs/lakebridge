import dataclasses
import tempfile
from pathlib import Path
from collections.abc import Callable

from databricks.labs.blueprint.entrypoint import get_logger
from databricks.labs.blueprint.tui import Prompts

from databricks.labs.bladespector.analyzer import Analyzer

from databricks.labs.lakebridge.helpers.file_utils import check_path, move_tmp_file

logger = get_logger(__file__)


@dataclasses.dataclass
class AnalyzerResult:
    source_directory: Path
    output_directory: Path
    source_system: str


class AnalyzerPrompts:

    def __init__(self, prompts: Prompts):
        self._prompts = prompts

    def get_source_directory(self) -> Path:
        """Get and validate the source directory from user input."""
        directory_str = self._prompts.question(
            "Enter full path to the source directory",
            default=Path.cwd().as_posix(),
            validate=check_path,
        )
        return Path(directory_str).resolve()

    def get_result_file_path(self, directory: Path) -> Path:
        """Get the result file path - accepts either filename or full path."""
        filename = self._prompts.question(
            "Enter report file name or custom export path including file name without extension",
            default=f"{directory.as_posix()}/lakebridge-analyzer-results.xlsx",
            validate=check_path,
        )
        return directory / Path(filename) if len(filename.split("/")) == 1 else Path(filename)

    def get_source_system(self, platform: str | None = None) -> str:
        """Validate source technology or prompt for a valid source"""
        if platform is None or platform not in Analyzer.supported_source_technologies():
            if platform is not None:
                logger.warning(f"Invalid source technology {platform}")
            platform = self._prompts.choice("Select the source technology", Analyzer.supported_source_technologies())
        assert platform in Analyzer.supported_source_technologies()

        return platform


class AnalyzerRunner:
    def __init__(
        self, runnable: Callable[[Path, Path, str, bool], None], move_file: Callable[[Path, Path], None], is_debug: bool
    ):
        self._runnable = runnable
        self._move_file = move_file
        self._is_debug = is_debug

    @classmethod
    def create(cls, is_debug: bool = False) -> "AnalyzerRunner":
        return cls(Analyzer.analyze, move_tmp_file, is_debug)

    def run(self, source_dir: Path, results_dir: Path, platform: str) -> AnalyzerResult:
        logger.debug(f"Starting analyzer execution in ${source_dir} for ${platform}")

        if not check_path(source_dir) or not check_path(results_dir):
            raise ValueError(f"Invalid path(s) provided: source_dir={source_dir}, results_dir={results_dir}")

        tmp_dir = self._temp_xlsx_path(results_dir)
        self._runnable(source_dir, tmp_dir, platform, self._is_debug)
        self._move_file(tmp_dir, Path(results_dir))
        logger.info(f"Successfully Analyzed files in ${source_dir} for ${platform} and saved report to {results_dir}")
        return AnalyzerResult(Path(source_dir), Path(results_dir), platform)

    @staticmethod
    def _temp_xlsx_path(results_dir: Path | str) -> Path:
        return (Path(tempfile.mkdtemp()) / Path(results_dir).name).with_suffix(".xlsx")


class LakebridgeAnalyzer:

    def __init__(self, prompts: AnalyzerPrompts, runner: AnalyzerRunner):
        self._prompts = prompts
        self._runner = runner

    def run_analyzer(
        self, source: str | None = None, results: str | None = None, platform: str | None = None
    ) -> AnalyzerResult:
        if not source:
            source_dir = self._prompts.get_source_directory()
        elif not isinstance(source, Path):
            source_dir = Path(source)
        else:
            source_dir = source

        if not results:
            results_dir = self._prompts.get_result_file_path(source_dir)
        elif not isinstance(results, Path):
            results_dir = Path(results)
        else:
            results_dir = results

        platform = self._prompts.get_source_system(platform)

        return self._runner.run(source_dir, results_dir, platform)
