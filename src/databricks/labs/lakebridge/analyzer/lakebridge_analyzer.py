import tempfile
from pathlib import Path

from databricks.sdk.service.iam import User
from databricks.sdk.core import with_user_agent_extra

from databricks.labs.blueprint.entrypoint import get_logger
from databricks.labs.blueprint.tui import Prompts

from databricks.labs.bladespector.analyzer import Analyzer, _PLATFORM_TO_SOURCE_TECHNOLOGY

from databricks.labs.lakebridge.helpers.telemetry_utils import make_alphanum_or_semver
from databricks.labs.lakebridge.helpers.file_utils import check_path, move_tmp_file

logger = get_logger(__file__)


class LakebridgeAnalyzer(Analyzer):
    def __init__(self, current_user: User, prompts: Prompts, is_debug: bool = False):
        self._current_user = current_user
        self._prompts = prompts
        self._is_debug = is_debug
        super().__init__()

    def _get_source_directory(self) -> Path:
        """Get and validate the source directory from user input."""
        directory_str = self._prompts.question(
            "Enter full path to the source directory",
            default=Path.cwd().as_posix(),
            validate=check_path,
        )
        return Path(directory_str).resolve()

    def _get_result_file_path(self, directory: Path) -> Path:
        """Get the result file path - accepts either filename or full path."""
        filename = self._prompts.question(
            "Enter report file name or custom export path including file name without extension",
            default=f"{directory.as_posix()}/lakebridge-analyzer-results.xlsx",
            validate=check_path,
        )
        return directory / Path(filename) if len(filename.split("/")) == 1 else Path(filename)

    def _get_source_tech(self, platform: str | None = None) -> str:
        """Validate source technology or prompt for a valid source"""
        if platform is None or platform not in self.supported_source_technologies():
            if platform is not None:
                logger.warning(f"Invalid source technology {platform}")
            platform = self._prompts.choice("Select the source technology", self.supported_source_technologies())
            with_user_agent_extra("analyzer_source_tech", make_alphanum_or_semver(platform))
            logger.debug(f"User: {self._current_user}")
        return _PLATFORM_TO_SOURCE_TECHNOLOGY[platform]

    @staticmethod
    def _temp_xlsx_path(results_dir: Path | str) -> Path:
        return (Path(tempfile.mkdtemp()) / Path(results_dir).name).with_suffix(".xlsx")

    def _run_prompt_analyzer(self):
        """Run the analyzer: prompt guided"""
        source_dir = self._get_source_directory()
        results_dir = self._get_result_file_path(source_dir)
        tmp_dir = self._temp_xlsx_path(results_dir)
        technology = self._get_source_tech()

        self._run_binary(source_dir, tmp_dir, technology, self._is_debug)

        move_tmp_file(tmp_dir, results_dir)

        logger.info(f"Successfully Analyzed files in ${source_dir} for ${technology} and saved report to {results_dir}")

    def _run_arg_analyzer(self, source_dir: str | None, results_dir: str | None, technology: str | None):
        """Run the analyzer: arg guided"""
        if source_dir is None or results_dir is None or technology is None:
            logger.error("All arguments (--source-directory, --report-file, --source-tech) must be provided")
            return

        if check_path(source_dir) and check_path(results_dir):
            tmp_dir = self._temp_xlsx_path(results_dir)
            technology = self._get_source_tech(technology)
            self._run_binary(Path(source_dir), tmp_dir, technology, self._is_debug)

            move_tmp_file(tmp_dir, Path(results_dir))

            logger.info(
                f"Successfully Analyzed files in ${source_dir} for ${technology} and saved report to {results_dir}"
            )

    def run_analyzer(
        self, source_dir: str | None = None, results_dir: str | None = None, technology: str | None = None
    ):
        """Run the analyzer."""
        if not any([source_dir, results_dir, technology]):
            self._run_prompt_analyzer()
            return

        self._run_arg_analyzer(source_dir, results_dir, technology)
