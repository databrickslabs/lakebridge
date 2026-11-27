"""Converts local sources with the agentic code converter, through its migration projects in the workspace."""

import json
import logging
import math
import re
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import NamedTuple, TypeVar

import requests
from databricks.sdk import WorkspaceClient
from databricks.sdk.errors import (
    AlreadyExists,
    BadRequest,
    DatabricksError,
    DeadlineExceeded,
    InternalError,
    InvalidParameterValue,
    NotFound,
    ResourceAlreadyExists,
    TemporarilyUnavailable,
)
from databricks.sdk.service.geniemigrate import (
    ConversionRun,
    ConversionRunState,
    ConvertProjectFilesOperation,
    FileStatus,
    Location,
    Project,
    ProjectConfig,
    ProjectFile,
    SourceDialect,
    SourceFileRef,
    TargetFramework,
    WorkspaceLocation,
)
from databricks.sdk.service.workspace import ImportFormat

from databricks.labs.lakebridge.transpiler.agentic.outputs import ConvertedFile, OutputWriter
from databricks.labs.lakebridge.transpiler.agentic.sources import SourceFiles
from databricks.labs.lakebridge.transpiler.transpile_status import ErrorKind, ErrorSeverity, TranspileError

logger = logging.getLogger(__name__)

AGENTIC_TRANSPILER = "agentic"
"""The `--transpiler` value that selects the agentic code converter."""


class Dialect(NamedTuple):
    value: SourceDialect
    label: str
    """The name the project is named with."""


class Target(NamedTuple):
    value: TargetFramework
    label: str
    """The name the project is named with."""


SOURCE_DIALECTS: dict[str, Dialect] = {
    "bigquery": Dialect(SourceDialect.SOURCE_DIALECT_BIGQUERY, "BigQuery"),
    "mssql": Dialect(SourceDialect.SOURCE_DIALECT_T_SQL, "T-SQL"),
    "oracle": Dialect(SourceDialect.SOURCE_DIALECT_ORACLE, "Oracle"),
    "redshift": Dialect(SourceDialect.SOURCE_DIALECT_REDSHIFT, "Redshift"),
    "snowflake": Dialect(SourceDialect.SOURCE_DIALECT_SNOWFLAKE, "Snowflake"),
    "synapse": Dialect(SourceDialect.SOURCE_DIALECT_T_SQL, "T-SQL"),
    "teradata": Dialect(SourceDialect.SOURCE_DIALECT_TERADATA, "Teradata"),
}
"""The `--source-dialect` values the agentic code converter takes."""

TARGET_TECHNOLOGIES: dict[str, Target] = {
    "SPARKSQL": Target(TargetFramework.TARGET_FRAMEWORK_SQL, "DBSQL"),
    "PYSPARK": Target(TargetFramework.TARGET_FRAMEWORK_PYSPARK, "Notebooks"),
}
"""The `--target-technology` values: Databricks SQL queries, or notebooks."""

MAX_SKILLS = 10
"""The most custom skills a conversion takes."""
MAX_SKILLS_BYTES = 1000
"""The most bytes the custom skill paths take, as a JSON list."""

FILES_PER_ADD = 500
"""The most files one request adds to a project."""
FILES_PER_RUN = 500
"""The most files one conversion run takes: the service's cap. A workspace can set a lower limit; runs then shrink."""
UPLOAD_TIMEOUT_SECONDS = 900
"""Importing an archive of up to 2000 files can take minutes, far past the default 60-second HTTP timeout."""
POLL_SECONDS = 15
"""How often to check on a conversion run."""
MAX_FAILED_CHECKS = 5
"""How many checks on a conversion can fail in a row before Lakebridge stops following it."""

_NO_FILES_NEED_CONVERSION = "No files need conversion"
_FINISHED = frozenset(
    {
        FileStatus.FILE_STATUS_SUCCESS,
        FileStatus.FILE_STATUS_PARTIAL_SUCCESS,
        FileStatus.FILE_STATUS_ERROR,
        FileStatus.FILE_STATUS_REVIEWED,
    }
)
_CONVERTED = frozenset(
    {FileStatus.FILE_STATUS_SUCCESS, FileStatus.FILE_STATUS_PARTIAL_SUCCESS, FileStatus.FILE_STATUS_REVIEWED}
)
_RETRYABLE = (InternalError, TemporarilyUnavailable, DeadlineExceeded, requests.exceptions.Timeout, TimeoutError)
_API_ERRORS = (DatabricksError, requests.exceptions.RequestException, TimeoutError)
"""What a workspace call can raise once the SDK's own retries are used up."""
_NOT_AVAILABLE = frozenset({"FEATURE_DISABLED", "NOT_IMPLEMENTED"})
"""Error codes that mean the workspace can't convert files, rather than that checking failed."""
_T = TypeVar("_T")


def workspace_relative(path: str) -> str:
    """A workspace path without the `/Workspace` prefix: responses may leave it out, requests may include it."""
    return path.removeprefix("/Workspace") if path.startswith("/Workspace/") else path


class AgenticConversionError(Exception):
    """The conversion couldn't run to the end; the message says what happened and where to follow up."""


@dataclass(frozen=True)
class AddedFile:
    """A source file, and the project file the migration project added it as."""

    source: PurePosixPath
    """The source file, relative to the input folder."""
    name: str
    """The project file's resource name, `projects/<id>/files/<file_id>`."""


@dataclass
class AgenticConversionResult:
    summary: dict[str, int | str | None]
    """The `transpile` summary: the same keys as the installed transpilers report."""
    error: str | None = None
    """Why the conversion didn't run to the end, if it didn't; the summary covers what it did."""
    written: dict[str, Path] = field(default_factory=dict)
    """The local output written for each project file."""
    project: str = ""
    """The migration project, `projects/<id>`."""
    run_folder: str = ""
    """The workspace folder holding the project, the uploaded sources and the converted files."""


class AgenticConverter:
    """Uploads the sources to the user's workspace, converts them in a new migration project, and writes the results.

    Each conversion gets its own run folder, `/Users/<user>/migrations/<dialect>/run_<timestamp>`, holding the project,
    the uploaded sources (`src/`) and the converted files (`converted/`). Runs convert the files in batches up to the
    per-run limit, one after another, each naming its files.
    """

    def __init__(
        self,
        ws: WorkspaceClient,
        *,
        long_ws: WorkspaceClient | None = None,
        poll_seconds: float = POLL_SECONDS,
        sleep: Callable[[float], None] = time.sleep,
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self._ws = ws
        self._long_ws = long_ws
        self._poll_seconds = poll_seconds
        self._sleep = sleep
        self._now = now

    def convert(
        self,
        *,
        sources: SourceFiles,
        source_dialect: str,
        target_technology: str,
        user_name: str,
        output_folder: Path,
        error_path: Path | None,
        skill_paths: Sequence[str] = (),
    ) -> AgenticConversionResult:
        dialect = SOURCE_DIALECTS[source_dialect]
        target = TARGET_TECHNOLOGIES[target_technology]
        run = self._run_folder(user_name, source_dialect)
        folder_id = self._prepare_run_folder(run)
        project = self._create_project(
            run, folder_id, dialect.value, target.value, f"Migration Project - {dialect.label} to {target.label}"
        )
        url = self._project_url(project)
        try:
            self._check_enabled(project, list(skill_paths))
        except AgenticConversionError:
            self._delete_project(project)
            self._delete_folder(run)
            raise
        self._upload(sources, run, project)
        added = self._add_files(sources, run, project)
        logger.info(f"Converting {len(added)} files in the migration project: {url}")
        error = None
        try:
            self._convert(project, added, list(skill_paths))
        except AgenticConversionError as err:
            error = f"{err} The project: {url}"
        except KeyboardInterrupt as err:
            msg = f"Stopped waiting. The conversion keeps running in the project: {url}"
            raise AgenticConversionError(msg) from err
        try:
            files = self._check(lambda: list(self._ws.genie_migrate.list_files(project)))
        except AgenticConversionError as err:
            msg = f"Could not read the conversion's results: {err} The converted files are in the project: {url}"
            raise AgenticConversionError(msg) from err
        result = self._results(run, url, files, sources, output_folder, error_path, error)
        result.project, result.run_folder = project, run
        return result

    def _run_folder(self, user_name: str, source_dialect: str) -> str:
        dialect = re.sub(r"[^A-Za-z0-9._-]+", "_", source_dialect).strip("_.-") or "unknown"
        return f"/Users/{user_name}/migrations/{dialect}/run_{self._now().strftime('%Y-%m-%d_%H%M%S')}"

    def _prepare_run_folder(self, run: str) -> int:
        try:
            try:
                has_content = any(True for _ in self._ws.workspace.list(run))
            except NotFound:
                has_content = False
            if has_content:
                raise AgenticConversionError(f"The run folder already has content, try again: {run}")
            self._ws.workspace.mkdirs(f"{run}/converted")
            folder_id = self._ws.workspace.get_status(run).object_id
        except _API_ERRORS as err:
            raise AgenticConversionError(f"Could not create the run folder {run}: {err}") from err
        assert folder_id is not None, "The workspace returns an object id for an existing folder."
        return folder_id

    def _create_project(
        self, run: str, folder_id: int, dialect: SourceDialect, target: TargetFramework, display_name: str
    ) -> str:
        folder = f"folders/{folder_id}"
        project = Project(
            display_name=display_name,
            folder=folder,
            project_config=ProjectConfig(
                source_dialect=dialect,
                target_framework=target,
                destination_location=Location(workspace=WorkspaceLocation(path=f"{run}/converted")),
            ),
        )
        try:
            created = self._create_or_find(project, folder)
        except _API_ERRORS as err:
            raise AgenticConversionError(f"Could not create the migration project: {err}") from err
        if not created.name:
            raise AgenticConversionError("The migration project was created without a name.")
        logger.debug(f"Created the migration project {created.name} in {run}")
        return created.name

    def _create_or_find(self, project: Project, folder: str) -> Project:
        try:
            return self._ws.genie_migrate.create(project)
        except _RETRYABLE:
            # Creates aren't idempotent: one that timed out may have made the project, so look for it first.
            existing = next(iter(self._ws.genie_migrate.list(filter=f'folder = "{folder}"')), None)
            return existing if existing is not None else self._ws.genie_migrate.create(project)

    def _check_enabled(self, project: str, skill_paths: list[str]) -> None:
        """Converting the empty project starts nothing, and fails differently depending on whether it's enabled.

        The skill paths are checked too, so a bad one fails before anything is uploaded.
        """
        try:
            self._ws.genie_migrate.convert_project_files(parent=project, skill_paths=skill_paths or None)
        except DatabricksError as err:
            if _no_files_need_conversion(err):
                return
            if isinstance(err, InvalidParameterValue) and skill_paths:
                raise AgenticConversionError(f"Invalid value for '--skills': {err}") from err
            if isinstance(err, BadRequest) or err.error_code in _NOT_AVAILABLE:
                raise self._not_available(err) from err
            raise self._not_checked(err) from err
        except _API_ERRORS as err:
            raise self._not_checked(err) from err

    @staticmethod
    def _not_available(err: DatabricksError) -> AgenticConversionError:
        code = f"{err.error_code}: " if err.error_code else ""
        msg = (
            f"The agentic code converter can't convert files in this workspace ({code}{err}). Ask your workspace "
            "admin whether it's available, then try again."
        )
        return AgenticConversionError(msg)

    @staticmethod
    def _not_checked(err: Exception) -> AgenticConversionError:
        return AgenticConversionError(
            f"Could not check whether the agentic code converter is available ({err}), try again."
        )

    def _upload(self, sources: SourceFiles, run: str, project: str) -> None:
        archive = sources.archive("src")
        logger.info(f"Uploading {len(sources.relative_paths)} files to: {run}/src")
        workspace = self._long_client().workspace
        for attempt in (1, 2):
            try:
                # Imported as `src.zip` with format AUTO, the workspace expands the archive into `src/`.
                workspace.upload(f"{run}/src.zip", archive, format=ImportFormat.AUTO)
                return
            except _API_ERRORS as err:
                # A partly expanded folder makes the next import fail; an import that timed out may still finish.
                self._delete_folder(f"{run}/src")
                if attempt == 1 and isinstance(err, _RETRYABLE):
                    logger.info(f"Uploading again, the upload failed: {err}")
                    continue
                self._delete_project(project)
                raise AgenticConversionError(f"Could not upload the sources: {err}") from err

    def _long_client(self) -> WorkspaceClient:
        """A client with a long HTTP timeout, for uploading the sources and downloading the outputs."""
        if self._long_ws is None:
            config = self._ws.config.copy()
            config.http_timeout_seconds = UPLOAD_TIMEOUT_SECONDS
            self._long_ws = WorkspaceClient(config=config)
        return self._long_ws

    def _add_files(self, sources: SourceFiles, run: str, project: str) -> list[AddedFile]:
        """Adds the uploaded sources to the project; returns each source with the project file it was added as."""
        returned: list[ProjectFile] = []
        try:
            for start in range(0, len(sources.relative_paths), FILES_PER_ADD):
                batch = sources.relative_paths[start : start + FILES_PER_ADD]
                refs = [SourceFileRef(workspace=WorkspaceLocation(path=f"{run}/src/{path}")) for path in batch]
                returned.extend(self._ws.genie_migrate.add_files(project, refs).files or [])
        except _API_ERRORS as err:
            self._delete_project(project)
            raise AgenticConversionError(f"Could not add the sources to the migration project: {err}") from err
        # Matched on the returned source path, not on the position in the response.
        expected = {workspace_relative(f"{run}/src/{path}"): path for path in sources.relative_paths}
        names: dict[PurePosixPath, str] = {}
        for file in returned:
            path = file.source.workspace.path if file.source and file.source.workspace else None
            source = expected.get(workspace_relative(path)) if path else None
            if source is not None and file.name:
                names[source] = file.name
        missing = [path for path in sources.relative_paths if path not in names]
        if missing:
            self._delete_project(project)
            more = f" and {len(missing) - 1} other files" if len(missing) > 1 else ""
            raise AgenticConversionError(f"The migration project didn't return {missing[0]}{more}.")
        return [AddedFile(path, names[path]) for path in sources.relative_paths]

    def _convert(self, project: str, files: list[AddedFile], skill_paths: list[str]) -> None:
        """Converts the files in runs one after another, each naming its files, in source order."""
        names = [file.name for file in files]
        batch_size = FILES_PER_RUN
        start = number = 0
        while start < len(names):
            operation, batch = self._start_run(project, names[start : start + batch_size], skill_paths)
            batch_size = len(batch)
            number += 1
            runs = number + math.ceil((len(names) - start - len(batch)) / batch_size)
            run = self._wait(operation, number, runs)
            self._check_run(run, number, batch)
            start += len(batch)

    @staticmethod
    def _check_run(run: ConversionRun | None, number: int, batch: list[str]) -> None:
        state = run.state if run else None
        if state == ConversionRunState.CONVERSION_RUN_STATE_COMPLETED:
            assert run is not None
            reported = {file.name for file in run.files or []}
            missing = [name for name in batch if name not in reported]
            if missing:
                shown = ", ".join(missing[:5]) + (", …" if len(missing) > 5 else "")
                logger.warning(
                    f"Conversion run {number} didn't report {len(missing)} of its {len(batch)} files: {shown}"
                )
            if not any(file.status in _FINISHED for file in run.files or []):
                raise AgenticConversionError(f"Conversion run {number} finished without converting any file.")
            return
        if state == ConversionRunState.CONVERSION_RUN_STATE_FAILED:
            assert run is not None
            reason = f" ({run.error_code.value})" if run.error_code else ""
            raise AgenticConversionError(f"Conversion run {number} failed{reason}.")
        if state == ConversionRunState.CONVERSION_RUN_STATE_CANCELLED:
            raise AgenticConversionError(f"Conversion run {number} was cancelled.")
        raise AgenticConversionError(f"Conversion run {number} ended in an unknown state.")

    def _start_run(
        self, project: str, batch: list[str], skill_paths: list[str]
    ) -> tuple[ConvertProjectFilesOperation, list[str]]:
        """Starts a conversion run of the batch; returns its operation and the files it started with.

        A workspace can set a per-run limit below `FILES_PER_RUN`; going over it is rejected as an invalid value, so the
        batch is halved until it fits. Nothing else in the request can be invalid here: the skill paths were checked,
        the names are unique, and files in a new project can't be reviewed.
        """
        timeouts = 0
        while True:
            try:
                operation = self._ws.genie_migrate.convert_project_files(
                    parent=project, names=batch, skill_paths=skill_paths or None
                )
                return operation, batch
            except InvalidParameterValue as err:
                batch = _smaller_batch(batch, err)
            except (requests.exceptions.Timeout, TimeoutError) as err:
                # The run may have started anyway: starting again then finds it active, and stops.
                timeouts += 1
                if timeouts >= MAX_FAILED_CHECKS:
                    raise AgenticConversionError(f"Could not start a conversion run: {err}") from err
                logger.warning(f"Starting a conversion run timed out, trying again: {err}")
            except _API_ERRORS as err:
                raise _start_failed(err) from err

    def _wait(self, operation: ConvertProjectFilesOperation, number: int, runs: int) -> ConversionRun | None:
        progress = None
        while not self._check(operation.done):
            current = _progress(operation.metadata())
            if current != progress:
                logger.info(f"Conversion run {number} of {runs}: {current[0]} of {current[1]} files finished.")
                progress = current
            self._sleep(self._poll_seconds)
        run = operation.metadata()
        state = run.state.value.removeprefix("CONVERSION_RUN_STATE_") if run and run.state else "UNKNOWN"
        finished, total = _progress(run)
        logger.info(f"Conversion run {number} of {runs} ended {state}: {finished} of {total} files finished.")
        return run

    def _check(self, call: Callable[[], _T]) -> _T:
        """Checks on the conversion, tolerating a few failed checks in a row: one failed call doesn't end the wait."""
        failures = 0
        while True:
            try:
                return call()
            except _API_ERRORS as err:
                failures += 1
                if failures >= MAX_FAILED_CHECKS:
                    raise AgenticConversionError(f"Could not check on the conversion ({err}).") from err
                logger.warning(f"Checking on the conversion failed, trying again: {err}")
                self._sleep(self._poll_seconds)

    def _results(
        self,
        run: str,
        url: str,
        files: list[ProjectFile],
        sources: SourceFiles,
        output_folder: Path,
        error_path: Path | None,
        error: str | None,
    ) -> AgenticConversionResult:
        src_prefix = workspace_relative(f"{run}/src") + "/"
        by_source = sorted(((_relative_source(file, src_prefix), file) for file in files), key=lambda item: item[0])
        converted = [
            ConvertedFile(file.name, workspace_relative(destination), file.destination_kind)
            for _, file in by_source
            if file.name and file.status in _CONVERTED and (destination := _destination(file))
        ]
        writer = OutputWriter(self._long_client(), output_folder)
        written, export_errors = writer.write(f"{run}/converted", converted)
        failed = _report(by_source, written, sources.root)
        error_log_file = None
        if failed and error_path is not None:
            with error_path.open("a", encoding="utf-8") as errors:
                errors.writelines(f"{err}\n" for err in failed)
            error_log_file = str(error_path)
        if export_errors:
            for export_error in export_errors:
                logger.error(export_error)
            error = error or f"Some converted files couldn't be written locally; they're in the project: {url}"
        logger.info(f"The migration project: {url}")
        summary: dict[str, int | str | None] = {
            "total_files_processed": len(files),
            "total_queries_processed": len(written),
            "analysis_error_count": 0,
            "parsing_error_count": 0,
            "validation_error_count": 0,
            "generation_error_count": len(failed),
            "error_log_file": error_log_file,
        }
        return AgenticConversionResult(summary, error, written)

    def _project_url(self, project: str) -> str:
        host = (self._ws.config.host or "").rstrip("/")
        return f"{host}/editor/migrations/{project.removeprefix('projects/')}"

    def _delete_project(self, project: str) -> None:
        try:
            self._ws.genie_migrate.delete(project)
        except DatabricksError as err:
            logger.warning(f"Could not delete the migration project {project}: {err}")

    def _delete_folder(self, path: str) -> None:
        try:
            self._ws.workspace.delete(path, recursive=True)
        except NotFound:
            pass
        except DatabricksError as err:
            logger.warning(f"Could not delete {path}: {err}")


def _report(
    by_source: Sequence[tuple[PurePosixPath, ProjectFile]], written: dict[str, Path], root: Path
) -> list[TranspileError]:
    """Logs each file's result; returns the files that weren't converted, as errors."""
    failed = []
    for source, file in by_source:
        status = file.status.value.removeprefix("FILE_STATUS_") if file.status else "NOT_CONVERTED"
        if file.name in written:
            logger.info(f"{source}: {status}, written to {written[file.name]}")
        elif file.status in _CONVERTED:
            logger.warning(f"{source}: {status}, but its output couldn't be written locally")
        else:
            message = file.error_message or "The file wasn't converted."
            logger.warning(f"{source}: {status}: {message}")
            error = TranspileError(
                "AGENTIC_CONVERSION", ErrorKind.GENERATION, ErrorSeverity.ERROR, root / source, message
            )
            failed.append(error)
    return failed


def _smaller_batch(batch: list[str], err: InvalidParameterValue) -> list[str]:
    """Half the batch, after the workspace rejected it as over its per-run limit; fails if one file was rejected."""
    if len(batch) <= 1:
        raise AgenticConversionError(f"Could not start a conversion run: {err}") from err
    smaller = len(batch) // 2
    logger.info(f"The workspace's per-run limit is lower than {len(batch)} files, trying {smaller}.")
    return batch[:smaller]


def _start_failed(err: Exception) -> AgenticConversionError:
    """Why a conversion run couldn't start."""
    if isinstance(err, (AlreadyExists, ResourceAlreadyExists)):
        # A project runs one conversion at a time, and the active one isn't this command's to follow.
        return AgenticConversionError(
            f"A conversion run is already active in the project, so another can't start yet ({err})."
        )
    if isinstance(err, DatabricksError) and _no_files_need_conversion(err):
        return AgenticConversionError(f"The converter says these files don't need conversion: {err}")
    return AgenticConversionError(f"Could not start a conversion run: {err}")


def _no_files_need_conversion(err: DatabricksError) -> bool:
    """Whether converting failed because no file is left to convert. Matched on the message, not the error code."""
    return _NO_FILES_NEED_CONVERSION in str(err)


def _progress(run: ConversionRun | None) -> tuple[int, int]:
    """How many of a run's files are finished, and how many it has."""
    files = run.files if run and run.files else []
    return sum(1 for file in files if file.status in _FINISHED), len(files)


def _destination(file: ProjectFile) -> str | None:
    return file.destination.workspace.path if file.destination and file.destination.workspace else None


def _relative_source(file: ProjectFile, src_prefix: str) -> PurePosixPath:
    path = (
        workspace_relative(file.source.workspace.path)
        if file.source and file.source.workspace and file.source.workspace.path
        else ""
    )
    if path.startswith(src_prefix):
        return PurePosixPath(path.removeprefix(src_prefix))
    return PurePosixPath(PurePosixPath(path).name or (file.name or "unknown"))


def validate_skill_paths(skills: str) -> list[str]:
    """The `--skills` value, a comma-separated list of workspace skill folders, checked against the converter's limits."""
    paths = [path.strip() for path in skills.split(",") if path.strip()]
    if not paths:
        raise ValueError("Invalid value for '--skills': no skill folder given.")
    if len(paths) > MAX_SKILLS:
        raise ValueError(f"Invalid value for '--skills': at most {MAX_SKILLS} skill folders, got {len(paths)}.")
    for path in paths:
        if not path.startswith("/"):
            raise ValueError(f"Invalid value for '--skills': {path!r} must be a workspace path, starting with '/'.")
    if len(json.dumps(paths, separators=(",", ":")).encode()) > MAX_SKILLS_BYTES:
        raise ValueError(f"Invalid value for '--skills': the skill paths must fit in {MAX_SKILLS_BYTES} bytes.")
    return paths
