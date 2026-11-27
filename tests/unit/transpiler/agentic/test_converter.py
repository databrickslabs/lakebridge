import base64
import io
import json
import logging
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import call, create_autospec

import pytest
import requests
from databricks.sdk import WorkspaceClient
from databricks.sdk.errors import (
    AlreadyExists,
    BadRequest,
    DatabricksError,
    InternalError,
    InvalidParameterValue,
    NotFound,
    PermissionDenied,
)
from databricks.sdk.service.geniemigrate import (
    AddProjectFilesResponse,
    ConversionRun,
    ConversionRunErrorCode,
    ConversionRunFile,
    ConversionRunState,
    DestinationKind,
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
from databricks.sdk.service.workspace import ExportFormat, ExportResponse, ImportFormat, ObjectInfo

from databricks.labs.lakebridge.transpiler.agentic import converter as converter_module
from databricks.labs.lakebridge.transpiler.agentic.converter import (
    AgenticConversionError,
    AgenticConverter,
    validate_skill_paths,
)
from databricks.labs.lakebridge.transpiler.agentic.sources import SourceFiles

USER = "someone@example.com"
RUN = f"/Users/{USER}/migrations/snowflake/run_2026-11-26_093000"
PROJECT = "projects/7"
URL = "https://example.cloud.databricks.com/editor/migrations/7"
NO_FILES = BadRequest("No files need conversion")


class FakeOperation:
    """A conversion run's operation: each `done()` moves to the next state, as re-fetching it would."""

    def __init__(self, *states: ConversionRun) -> None:
        self._states = list(states)
        self._current = 0

    def done(self) -> bool:
        if self._current < len(self._states) - 1:
            self._current += 1
        return self._current == len(self._states) - 1

    def metadata(self) -> ConversionRun:
        return self._states[self._current]

    def name(self) -> str:
        return f"{PROJECT}/operations/conversion-run.1"


def _run(
    state: ConversionRunState,
    *statuses: FileStatus,
    error_code: ConversionRunErrorCode | None = None,
    names: list[str] | None = None,
):
    names = names or [f"{PROJECT}/files/{i}" for i in range(len(statuses))]
    files = [ConversionRunFile(name=name, status=status) for name, status in zip(names, statuses)]
    return ConversionRun(project=PROJECT, state=state, files=files, error_code=error_code)


ACTIVE = ConversionRunState.CONVERSION_RUN_STATE_ACTIVE
COMPLETED = ConversionRunState.CONVERSION_RUN_STATE_COMPLETED


def _completed_operation(*statuses: FileStatus, names: list[str] | None = None) -> FakeOperation:
    queued = [FileStatus.FILE_STATUS_QUEUED for _ in statuses]
    return FakeOperation(_run(ACTIVE, *queued, names=names), _run(COMPLETED, *statuses, names=names))


def _succeeded(count: int) -> FakeOperation:
    return _completed_operation(*[FileStatus.FILE_STATUS_SUCCESS] * count)


def _added_files(project: str, refs: list[SourceFileRef]) -> AddProjectFilesResponse:
    """What files:add returns: each file's name, and its path, with the `/Workspace` prefix responses can add."""
    files = []
    for ref in refs:
        assert ref.workspace and ref.workspace.path
        source = ref.workspace.path.split("/src/", 1)[1]
        workspace = WorkspaceLocation(path=f"/Workspace{ref.workspace.path}")
        files.append(ProjectFile(name=f"{project}/files/{source}", source=Location(workspace=workspace)))
    return AddProjectFilesResponse(files=files)


def _write_files(root: Path, count: int) -> SourceFiles:
    root.mkdir(parents=True, exist_ok=True)
    for i in range(count):
        (root / f"f{i:04d}.sql").write_text("SELECT 1;")
    return SourceFiles.collect(root)


def _project_file(
    source: str,
    status: FileStatus | None,
    destination: str | None = None,
    kind: DestinationKind | None = None,
    error_message: str | None = None,
) -> ProjectFile:
    return ProjectFile(
        name=f"{PROJECT}/files/{source}",
        source=Location(workspace=WorkspaceLocation(path=f"/Workspace{RUN}/src/{source}")),
        destination=Location(workspace=WorkspaceLocation(path=destination)) if destination else None,
        destination_kind=kind,
        status=status,
        error_message=error_message,
    )


def _zip(**entries: str) -> str:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("converted/", b"")
        for name, content in entries.items():
            archive.writestr(f"converted/{name}", content)
        archive.writestr("manifest.mf", "Manifest-Version: 1.0\n")
    return base64.b64encode(buffer.getvalue()).decode()


def _folder(**entries: str) -> io.BytesIO:
    """What downloading the converted folder streams: a zip of its entries, as base64-decoded bytes."""
    return io.BytesIO(base64.b64decode(_zip(**entries)))


def _encoded(content: str) -> str:
    return base64.b64encode(content.encode()).decode()


@pytest.fixture(name="ws")
def fixture_ws() -> WorkspaceClient:
    workspace = create_autospec(WorkspaceClient)
    workspace.config.host = "https://example.cloud.databricks.com/"
    workspace.workspace.list.side_effect = NotFound("does not exist")
    workspace.workspace.get_status.return_value = ObjectInfo(path=RUN, object_id=42)
    workspace.genie_migrate.add_files.side_effect = _added_files
    workspace.genie_migrate.create.return_value = Project(
        name=PROJECT,
        display_name="ignored",
        project_config=ProjectConfig(target_framework=TargetFramework.TARGET_FRAMEWORK_PYSPARK),
    )
    return workspace


@pytest.fixture(name="upload_ws")
def fixture_upload_ws() -> WorkspaceClient:
    return create_autospec(WorkspaceClient)


@pytest.fixture(name="sources")
def fixture_sources(tmp_path: Path) -> SourceFiles:
    root = tmp_path / "input"
    for path in ("customers.sql", "nested/orders.sql"):
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_text("SELECT 1;\n")
    return SourceFiles.collect(root)


def _converter(ws: WorkspaceClient, upload_ws: WorkspaceClient) -> AgenticConverter:
    now = datetime(2026, 11, 26, 9, 30, tzinfo=timezone.utc)
    return AgenticConverter(ws, long_ws=upload_ws, sleep=lambda _: None, now=lambda: now)


def _convert(converter: AgenticConverter, sources: SourceFiles, tmp_path: Path, target: str = "PYSPARK", **kwargs):
    return converter.convert(
        sources=sources,
        source_dialect="snowflake",
        target_technology=target,
        user_name=USER,
        output_folder=tmp_path / "output",
        error_path=kwargs.pop("error_path", None),
        **kwargs,
    )


def test_converts_the_sources_and_writes_each_notebook_flat(ws, upload_ws, sources, tmp_path: Path, caplog) -> None:
    ws.genie_migrate.convert_project_files.side_effect = [
        NO_FILES,
        _completed_operation(FileStatus.FILE_STATUS_SUCCESS, FileStatus.FILE_STATUS_PARTIAL_SUCCESS),
    ]
    notebook = DestinationKind.DESTINATION_KIND_NOTEBOOK
    ws.genie_migrate.list_files.return_value = [
        _project_file("nested/orders.sql", FileStatus.FILE_STATUS_PARTIAL_SUCCESS, f"{RUN}/converted/orders", notebook),
        _project_file("customers.sql", FileStatus.FILE_STATUS_SUCCESS, f"{RUN}/converted/customers", notebook),
    ]
    upload_ws.workspace.download.return_value = _folder(**{"customers.py": "# c", "orders.py": "# o"})

    with caplog.at_level(logging.INFO):
        result = _convert(_converter(ws, upload_ws), sources, tmp_path, skill_paths=["/Workspace/skills/style"])

    output = tmp_path / "output"
    assert (output / "customers.py").read_text() == "# c"
    assert (output / "orders.py").read_text() == "# o"
    assert result.error is None
    assert result.summary == {
        "total_files_processed": 2,
        "total_queries_processed": 2,
        "analysis_error_count": 0,
        "parsing_error_count": 0,
        "validation_error_count": 0,
        "generation_error_count": 0,
        "error_log_file": None,
    }
    ws.workspace.mkdirs.assert_called_once_with(f"{RUN}/converted")
    ws.genie_migrate.create.assert_called_once_with(
        Project(
            display_name="Migration Project - Snowflake to Notebooks",
            folder="folders/42",
            project_config=ProjectConfig(
                source_dialect=SourceDialect.SOURCE_DIALECT_SNOWFLAKE,
                target_framework=TargetFramework.TARGET_FRAMEWORK_PYSPARK,
                destination_location=Location(workspace=WorkspaceLocation(path=f"{RUN}/converted")),
            ),
        )
    )
    upload_ws.workspace.upload.assert_called_once_with(
        f"{RUN}/src.zip", sources.archive("src"), format=ImportFormat.AUTO
    )
    ws.genie_migrate.add_files.assert_called_once_with(
        PROJECT,
        [
            SourceFileRef(workspace=WorkspaceLocation(path=f"{RUN}/src/customers.sql")),
            SourceFileRef(workspace=WorkspaceLocation(path=f"{RUN}/src/nested/orders.sql")),
        ],
    )
    assert ws.genie_migrate.convert_project_files.call_args_list == [
        call(parent=PROJECT, skill_paths=["/Workspace/skills/style"]),
        call(
            parent=PROJECT,
            names=[f"{PROJECT}/files/customers.sql", f"{PROJECT}/files/nested/orders.sql"],
            skill_paths=["/Workspace/skills/style"],
        ),
    ]
    upload_ws.workspace.download.assert_called_once_with(f"{RUN}/converted", format=ExportFormat.AUTO)
    upload_ws.workspace.export.assert_not_called()
    assert URL in caplog.text


def test_writes_each_databricks_sql_query_as_a_sql_file(ws, upload_ws, sources, tmp_path: Path) -> None:
    ws.genie_migrate.convert_project_files.side_effect = [
        NO_FILES,
        _completed_operation(FileStatus.FILE_STATUS_SUCCESS),
    ]
    query = DestinationKind.DESTINATION_KIND_QUERY
    # Responses can leave out the `/Workspace` prefix.
    ws.genie_migrate.list_files.return_value = [
        _project_file(
            "nested/orders.sql", FileStatus.FILE_STATUS_SUCCESS, f"{RUN}/converted/orders.dbquery.ipynb", query
        )
    ]
    notebook = {"cells": [{"cell_type": "code", "source": ["SELECT 1;\n", "SELECT 2;\n"]}, {"cell_type": "markdown"}]}
    upload_ws.workspace.download.return_value = _folder(**{"orders.dbquery.ipynb": json.dumps(notebook)})

    result = _convert(_converter(ws, upload_ws), sources, tmp_path, target="SPARKSQL")

    assert (tmp_path / "output" / "orders.sql").read_text() == "SELECT 1;\nSELECT 2;\n"
    assert result.written == {f"{PROJECT}/files/nested/orders.sql": tmp_path / "output" / "orders.sql"}
    # The AUTO folder download includes queries, so nothing is exported on its own.
    upload_ws.workspace.export.assert_not_called()
    assert ws.genie_migrate.create.call_args.args[0].display_name == "Migration Project - Snowflake to DBSQL"


def test_starts_runs_one_after_another_each_naming_its_files(
    ws, upload_ws, sources, tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(converter_module, "FILES_PER_RUN", 1)
    ws.genie_migrate.convert_project_files.side_effect = [
        NO_FILES,
        _completed_operation(FileStatus.FILE_STATUS_SUCCESS),
        _completed_operation(FileStatus.FILE_STATUS_ERROR),
    ]
    ws.genie_migrate.list_files.return_value = []

    result = _convert(_converter(ws, upload_ws), sources, tmp_path)

    assert result.error is None
    started = [c.kwargs["names"] for c in ws.genie_migrate.convert_project_files.call_args_list[1:]]
    assert started == [[f"{PROJECT}/files/customers.sql"], [f"{PROJECT}/files/nested/orders.sql"]]


def test_adds_the_files_in_batches(ws, upload_ws, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(converter_module, "FILES_PER_ADD", 2)
    root = tmp_path / "input"
    root.mkdir()
    for name in ("a", "b", "c"):
        (root / f"{name}.sql").write_text("SELECT 1;")
    ws.genie_migrate.convert_project_files.side_effect = [NO_FILES, _succeeded(3)]
    ws.genie_migrate.list_files.return_value = []

    _convert(_converter(ws, upload_ws), SourceFiles.collect(root), tmp_path)

    batches = [len(c.args[1]) for c in ws.genie_migrate.add_files.call_args_list]
    assert batches == [2, 1]


@pytest.mark.parametrize(
    ("final", "expected"),
    [
        (
            _run(
                ConversionRunState.CONVERSION_RUN_STATE_FAILED,
                error_code=ConversionRunErrorCode.CONVERSION_RUN_ERROR_CODE_EXPIRED,
            ),
            "Conversion run 1 failed (CONVERSION_RUN_ERROR_CODE_EXPIRED).",
        ),
        (
            _run(ConversionRunState.CONVERSION_RUN_STATE_CANCELLED, FileStatus.FILE_STATUS_STOPPED),
            "Conversion run 1 was cancelled.",
        ),
        (_run(COMPLETED, FileStatus.FILE_STATUS_STOPPED), "Conversion run 1 finished without converting any file."),
        (ConversionRun(project=PROJECT), "Conversion run 1 ended in an unknown state."),
    ],
)
def test_reports_a_run_that_did_not_complete_and_still_writes_what_was_converted(
    ws, upload_ws, sources, tmp_path: Path, final: ConversionRun, expected: str
) -> None:
    ws.genie_migrate.convert_project_files.side_effect = [NO_FILES, FakeOperation(_run(ACTIVE), final)]
    ws.genie_migrate.list_files.return_value = [
        _project_file(
            "customers.sql",
            FileStatus.FILE_STATUS_SUCCESS,
            f"{RUN}/converted/customers.json",
            DestinationKind.DESTINATION_KIND_FILE,
        ),
        _project_file("nested/orders.sql", FileStatus.FILE_STATUS_STOPPED),
    ]
    upload_ws.workspace.download.return_value = _folder(**{"customers.json": "{}"})

    result = _convert(_converter(ws, upload_ws), sources, tmp_path)

    assert result.error == f"{expected} The project: {URL}"
    assert (tmp_path / "output" / "customers.json").read_text() == "{}"
    assert result.summary["generation_error_count"] == 1


ACTIVE_RUN = f"A conversion run is already active in the project, so another can't start yet (a run is active). The project: {URL}"


def test_stops_when_a_conversion_run_is_already_active(ws, upload_ws, sources, tmp_path: Path) -> None:
    ws.genie_migrate.convert_project_files.side_effect = [NO_FILES, AlreadyExists("a run is active")]
    ws.genie_migrate.list_files.return_value = []

    result = _convert(_converter(ws, upload_ws), sources, tmp_path)

    assert result.error == ACTIVE_RUN
    assert ws.genie_migrate.convert_project_files.call_count == 2


@pytest.mark.parametrize(
    "error",
    [
        DatabricksError("Conversion runs are not enabled for this workspace", error_code="FEATURE_DISABLED"),
        BadRequest("Conversion runs require an EntityStore project"),
    ],
)
def test_fails_before_uploading_when_the_workspace_cant_convert(ws, upload_ws, sources, tmp_path, error) -> None:
    ws.genie_migrate.convert_project_files.side_effect = [error]

    with pytest.raises(AgenticConversionError, match="can't convert files in this workspace"):
        _convert(_converter(ws, upload_ws), sources, tmp_path)

    ws.genie_migrate.delete.assert_called_once_with(PROJECT)
    ws.workspace.delete.assert_called_once_with(RUN, recursive=True)
    upload_ws.workspace.upload.assert_not_called()


def test_refuses_a_run_folder_that_already_has_content(ws, upload_ws, sources, tmp_path: Path) -> None:
    ws.workspace.list.side_effect = None
    ws.workspace.list.return_value = iter([ObjectInfo(path=f"{RUN}/src")])

    with pytest.raises(AgenticConversionError, match="already has content"):
        _convert(_converter(ws, upload_ws), sources, tmp_path)

    ws.genie_migrate.create.assert_not_called()


def test_uploads_again_after_a_timeout_once_the_partial_upload_is_removed(ws, upload_ws, sources, tmp_path) -> None:
    upload_ws.workspace.upload.side_effect = [requests.exceptions.ReadTimeout("timed out"), None]
    ws.genie_migrate.convert_project_files.side_effect = [NO_FILES, _succeeded(2)]
    ws.genie_migrate.list_files.return_value = []

    result = _convert(_converter(ws, upload_ws), sources, tmp_path)

    assert result.error is None
    assert upload_ws.workspace.upload.call_count == 2
    ws.workspace.delete.assert_called_once_with(f"{RUN}/src", recursive=True)


def test_deletes_the_project_when_the_upload_fails(ws, upload_ws, sources, tmp_path: Path) -> None:
    upload_ws.workspace.upload.side_effect = DatabricksError(
        "Workspace files are disabled", error_code="FEATURE_DISABLED"
    )
    ws.genie_migrate.convert_project_files.side_effect = [NO_FILES]

    with pytest.raises(AgenticConversionError, match="Could not upload the sources"):
        _convert(_converter(ws, upload_ws), sources, tmp_path)

    assert upload_ws.workspace.upload.call_count == 1
    ws.genie_migrate.delete.assert_called_once_with(PROJECT)


def test_deletes_the_project_when_adding_the_files_fails(ws, upload_ws, sources, tmp_path: Path) -> None:
    ws.genie_migrate.convert_project_files.side_effect = [NO_FILES]
    ws.genie_migrate.add_files.side_effect = BadRequest("duplicate sources")

    with pytest.raises(AgenticConversionError, match="Could not add the sources"):
        _convert(_converter(ws, upload_ws), sources, tmp_path)

    ws.genie_migrate.delete.assert_called_once_with(PROJECT)


def test_reuses_the_project_a_timed_out_create_made(ws, upload_ws, sources, tmp_path: Path) -> None:
    ws.genie_migrate.create.side_effect = InternalError("timed out")
    ws.genie_migrate.list.return_value = iter(
        [
            Project(
                name=PROJECT,
                display_name="x",
                project_config=ProjectConfig(target_framework=TargetFramework.TARGET_FRAMEWORK_PYSPARK),
            )
        ]
    )
    ws.genie_migrate.convert_project_files.side_effect = [NO_FILES, _succeeded(2)]
    ws.genie_migrate.list_files.return_value = []

    _convert(_converter(ws, upload_ws), sources, tmp_path)

    ws.genie_migrate.list.assert_called_once_with(filter='folder = "folders/42"')
    ws.genie_migrate.create.assert_called_once()
    ws.genie_migrate.add_files.assert_called_once()


def test_exports_files_one_by_one_when_the_folder_cant_be_exported(ws, upload_ws, sources, tmp_path: Path) -> None:
    ws.genie_migrate.convert_project_files.side_effect = [
        NO_FILES,
        _completed_operation(FileStatus.FILE_STATUS_SUCCESS),
    ]
    ws.genie_migrate.list_files.return_value = [
        _project_file(
            "customers.sql",
            FileStatus.FILE_STATUS_SUCCESS,
            f"{RUN}/converted/customers",
            DestinationKind.DESTINATION_KIND_NOTEBOOK,
        )
    ]
    upload_ws.workspace.download.side_effect = DatabricksError("Size of archive exceeds 209715200 bytes")
    upload_ws.workspace.export.return_value = ExportResponse(content=_encoded("# c"), file_type="py")

    result = _convert(_converter(ws, upload_ws), sources, tmp_path)

    assert result.error is None
    assert (tmp_path / "output" / "customers.py").read_text() == "# c"


def test_appends_the_files_that_were_not_converted_to_the_error_file(ws, upload_ws, sources, tmp_path: Path) -> None:
    error_path = tmp_path / "errors.log"
    error_path.write_text("earlier\n")
    ws.genie_migrate.convert_project_files.side_effect = [
        NO_FILES,
        _completed_operation(FileStatus.FILE_STATUS_ERROR, FileStatus.FILE_STATUS_SUCCESS),
    ]
    ws.genie_migrate.list_files.return_value = [
        _project_file("nested/orders.sql", FileStatus.FILE_STATUS_ERROR, error_message="Unsupported syntax"),
        _project_file("customers.sql", None),
    ]

    result = _convert(_converter(ws, upload_ws), sources, tmp_path, error_path=error_path)

    lines = error_path.read_text().splitlines()
    assert lines[0] == "earlier"
    assert "customers.sql" in lines[1] and "The file wasn't converted." in lines[1]
    assert "nested/orders.sql" in lines[2] and "Unsupported syntax" in lines[2]
    assert result.summary["generation_error_count"] == 2
    assert result.summary["error_log_file"] == str(error_path)


def test_ctrl_c_stops_waiting_and_names_the_project(ws, upload_ws, sources, tmp_path: Path) -> None:
    class Interrupted(FakeOperation):
        def done(self) -> bool:
            raise KeyboardInterrupt

    ws.genie_migrate.convert_project_files.side_effect = [NO_FILES, Interrupted(_run(ACTIVE))]

    with pytest.raises(AgenticConversionError, match=f"keeps running in the project: {URL}"):
        _convert(_converter(ws, upload_ws), sources, tmp_path)


class FlakyOperation(FakeOperation):
    """An operation whose first status checks fail."""

    def __init__(self, failures: int, *states: ConversionRun) -> None:
        super().__init__(*states)
        self._failures = failures

    def done(self) -> bool:
        if self._failures:
            self._failures -= 1
            raise InternalError("status check failed")
        return super().done()


def test_keeps_waiting_through_a_few_failed_status_checks(ws, upload_ws, sources, tmp_path: Path) -> None:
    run = FlakyOperation(
        2, _run(ACTIVE, FileStatus.FILE_STATUS_QUEUED), _run(COMPLETED, FileStatus.FILE_STATUS_SUCCESS)
    )
    ws.genie_migrate.convert_project_files.side_effect = [NO_FILES, run]
    ws.genie_migrate.list_files.return_value = []

    result = _convert(_converter(ws, upload_ws), sources, tmp_path)

    assert result.error is None


def test_stops_following_after_repeated_failed_checks_and_reports_what_finished(
    ws, upload_ws, sources, tmp_path: Path
) -> None:
    ws.genie_migrate.convert_project_files.side_effect = [NO_FILES, FlakyOperation(100, _run(ACTIVE))]
    notebook = DestinationKind.DESTINATION_KIND_NOTEBOOK
    ws.genie_migrate.list_files.return_value = [
        _project_file("customers.sql", FileStatus.FILE_STATUS_SUCCESS, f"{RUN}/converted/customers", notebook)
    ]
    upload_ws.workspace.download.return_value = _folder(**{"customers.py": "# c"})

    result = _convert(_converter(ws, upload_ws), sources, tmp_path)

    assert result.error is not None
    assert result.error.startswith("Could not check on the conversion (status check failed).")
    assert result.error.endswith(f"The project: {URL}")
    assert (tmp_path / "output" / "customers.py").read_text() == "# c"


def test_names_the_project_when_the_results_cant_be_read(ws, upload_ws, sources, tmp_path: Path) -> None:
    ws.genie_migrate.convert_project_files.side_effect = [NO_FILES, _succeeded(2)]
    ws.genie_migrate.list_files.side_effect = InternalError("listing failed")

    with pytest.raises(AgenticConversionError, match=f"Could not read the conversion's results.*{URL}"):
        _convert(_converter(ws, upload_ws), sources, tmp_path)


def test_starts_again_after_a_start_timed_out(ws, upload_ws, sources, tmp_path: Path) -> None:
    ws.genie_migrate.convert_project_files.side_effect = [
        NO_FILES,
        requests.exceptions.ReadTimeout("timed out"),
        _completed_operation(FileStatus.FILE_STATUS_SUCCESS),
    ]
    ws.genie_migrate.list_files.return_value = []

    result = _convert(_converter(ws, upload_ws), sources, tmp_path)

    assert result.error is None
    assert ws.genie_migrate.convert_project_files.call_count == 3


def test_stops_when_a_timed_out_start_went_through(ws, upload_ws, sources, tmp_path: Path) -> None:
    ws.genie_migrate.convert_project_files.side_effect = [
        NO_FILES,
        requests.exceptions.ReadTimeout("timed out"),
        AlreadyExists("a run is active"),
    ]
    ws.genie_migrate.list_files.return_value = []

    result = _convert(_converter(ws, upload_ws), sources, tmp_path)

    assert result.error == ACTIVE_RUN


def test_accepts_the_availability_answer_whatever_its_error_code(ws, upload_ws, sources, tmp_path: Path) -> None:
    no_files = DatabricksError("No files need conversion", error_code="FAILED_PRECONDITION")
    ws.genie_migrate.convert_project_files.side_effect = [no_files, _succeeded(2)]
    ws.genie_migrate.list_files.return_value = []

    result = _convert(_converter(ws, upload_ws), sources, tmp_path)

    assert result.error is None
    assert ws.genie_migrate.convert_project_files.call_count == 2


def test_fails_cleanly_when_the_run_folder_cant_be_created(ws, upload_ws, sources, tmp_path: Path) -> None:
    ws.workspace.mkdirs.side_effect = PermissionDenied("not allowed")

    with pytest.raises(AgenticConversionError, match=f"Could not create the run folder {RUN}: not allowed"):
        _convert(_converter(ws, upload_ws), sources, tmp_path)

    ws.genie_migrate.create.assert_not_called()


@pytest.mark.parametrize("error", [requests.exceptions.ReadTimeout("read timed out"), InternalError("server error")])
def test_says_it_couldnt_check_when_the_availability_check_fails(ws, upload_ws, sources, tmp_path, error) -> None:
    ws.genie_migrate.convert_project_files.side_effect = [error]

    with pytest.raises(AgenticConversionError, match="Could not check whether the agentic code converter is available"):
        _convert(_converter(ws, upload_ws), sources, tmp_path)

    ws.genie_migrate.delete.assert_called_once_with(PROJECT)
    ws.workspace.delete.assert_called_once_with(RUN, recursive=True)
    upload_ws.workspace.upload.assert_not_called()


def test_exports_files_one_by_one_when_the_folder_export_isnt_an_archive(ws, upload_ws, sources, tmp_path) -> None:
    ws.genie_migrate.convert_project_files.side_effect = [
        NO_FILES,
        _completed_operation(FileStatus.FILE_STATUS_SUCCESS),
    ]
    notebook = DestinationKind.DESTINATION_KIND_NOTEBOOK
    ws.genie_migrate.list_files.return_value = [
        _project_file("customers.sql", FileStatus.FILE_STATUS_SUCCESS, f"{RUN}/converted/customers", notebook)
    ]
    upload_ws.workspace.download.return_value = io.BytesIO(b"<html>not an archive</html>")
    upload_ws.workspace.export.return_value = ExportResponse(content=_encoded("# c"), file_type="py")

    result = _convert(_converter(ws, upload_ws), sources, tmp_path)

    assert result.error is None
    assert (tmp_path / "output" / "customers.py").read_text() == "# c"


def test_measures_the_skill_paths_as_json() -> None:
    # 1000 bytes as written, 1001 once the quote is escaped.
    path = "/" + "a" * 994 + '"'

    with pytest.raises(ValueError, match="must fit in 1000 bytes"):
        validate_skill_paths(path)
    assert validate_skill_paths("/" + "a" * 995) == ["/" + "a" * 995]


def test_rejects_a_bad_skill_path_before_uploading(ws, upload_ws, sources, tmp_path: Path) -> None:
    ws.genie_migrate.convert_project_files.side_effect = [
        InvalidParameterValue("Skill path /Workspace/x must be a folder directly inside one of: [...]")
    ]

    with pytest.raises(AgenticConversionError, match="Invalid value for '--skills': Skill path /Workspace/x must be"):
        _convert(_converter(ws, upload_ws), sources, tmp_path, skill_paths=["/Workspace/x"])

    ws.genie_migrate.convert_project_files.assert_called_once_with(parent=PROJECT, skill_paths=["/Workspace/x"])
    ws.genie_migrate.delete.assert_called_once_with(PROJECT)
    ws.workspace.delete.assert_called_once_with(RUN, recursive=True)
    upload_ws.workspace.upload.assert_not_called()


def test_converts_in_named_batches_in_source_order(ws, upload_ws, tmp_path: Path) -> None:
    sources = _write_files(tmp_path / "input", 1200)
    ws.genie_migrate.convert_project_files.side_effect = [NO_FILES, _succeeded(500), _succeeded(500), _succeeded(200)]
    ws.genie_migrate.list_files.return_value = []

    result = _convert(_converter(ws, upload_ws), sources, tmp_path)

    assert result.error is None
    calls = ws.genie_migrate.convert_project_files.call_args_list
    assert calls[0] == call(parent=PROJECT, skill_paths=None)
    started = [c.kwargs["names"] for c in calls[1:]]
    assert [len(names) for names in started] == [500, 500, 200]
    assert [name for names in started for name in names] == [f"{PROJECT}/files/f{i:04d}.sql" for i in range(1200)]
    assert [len(c.args[1]) for c in ws.genie_migrate.add_files.call_args_list] == [500, 500, 200]


def test_fails_when_the_project_doesnt_return_an_added_file(ws, upload_ws, sources, tmp_path: Path) -> None:
    ws.genie_migrate.convert_project_files.side_effect = [NO_FILES]
    ws.genie_migrate.add_files.side_effect = lambda project, refs: AddProjectFilesResponse(
        files=(_added_files(project, refs).files or [])[:1]
    )

    with pytest.raises(AgenticConversionError, match="The migration project didn't return nested/orders.sql."):
        _convert(_converter(ws, upload_ws), sources, tmp_path)

    ws.genie_migrate.delete.assert_called_once_with(PROJECT)
    ws.genie_migrate.convert_project_files.assert_called_once()


def test_shrinks_the_runs_to_the_workspace_limit(ws, upload_ws, tmp_path: Path, caplog) -> None:
    sources = _write_files(tmp_path / "input", 600)
    ws.genie_migrate.convert_project_files.side_effect = [
        NO_FILES,
        InvalidParameterValue("files per run 500 exceeds max 300"),
        _succeeded(250),
        _succeeded(250),
        _succeeded(100),
    ]
    ws.genie_migrate.list_files.return_value = []

    with caplog.at_level(logging.INFO):
        result = _convert(_converter(ws, upload_ws), sources, tmp_path)

    assert result.error is None
    sizes = [len(c.kwargs["names"]) for c in ws.genie_migrate.convert_project_files.call_args_list[1:]]
    assert sizes == [500, 250, 250, 100]
    assert "per-run limit is lower than 500 files, trying 250" in caplog.text
    assert "Conversion run 1 of 3" in caplog.text


def test_gives_up_when_even_one_file_is_rejected(ws, upload_ws, sources, tmp_path: Path) -> None:
    ws.genie_migrate.convert_project_files.side_effect = [
        NO_FILES,
        InvalidParameterValue("rejected"),
        InvalidParameterValue("rejected"),
    ]
    ws.genie_migrate.list_files.return_value = []

    result = _convert(_converter(ws, upload_ws), sources, tmp_path)

    assert result.error == f"Could not start a conversion run: rejected The project: {URL}"
    sizes = [len(c.kwargs["names"]) for c in ws.genie_migrate.convert_project_files.call_args_list[1:]]
    assert sizes == [2, 1]


def test_warns_when_a_run_reports_fewer_files_than_it_was_given(ws, upload_ws, sources, tmp_path, caplog) -> None:
    run = _completed_operation(FileStatus.FILE_STATUS_SUCCESS, names=[f"{PROJECT}/files/customers.sql"])
    ws.genie_migrate.convert_project_files.side_effect = [NO_FILES, run]
    ws.genie_migrate.list_files.return_value = []

    with caplog.at_level(logging.WARNING):
        result = _convert(_converter(ws, upload_ws), sources, tmp_path)

    assert result.error is None
    assert f"Conversion run 1 didn't report 1 of its 2 files: {PROJECT}/files/nested/orders.sql" in caplog.text


def test_stops_when_the_converter_says_named_files_dont_need_conversion(ws, upload_ws, sources, tmp_path) -> None:
    ws.genie_migrate.convert_project_files.side_effect = [NO_FILES, NO_FILES]
    ws.genie_migrate.list_files.return_value = []

    result = _convert(_converter(ws, upload_ws), sources, tmp_path)

    assert result.error is not None
    assert result.error.startswith("The converter says these files don't need conversion: No files need conversion")
