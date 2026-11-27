import base64
import io
import json
import zipfile
from pathlib import Path
from unittest.mock import create_autospec

import pytest
from databricks.sdk import WorkspaceClient
from databricks.sdk.errors import DatabricksError, NotFound
from databricks.sdk.service.geniemigrate import DestinationKind
from databricks.sdk.service.workspace import ExportFormat, ExportResponse

from databricks.labs.lakebridge.transpiler.agentic import outputs
from databricks.labs.lakebridge.transpiler.agentic.outputs import ConvertedFile, OutputWriter

CONVERTED = "/Users/someone@example.com/migrations/snowflake/run_1/converted"
NOTEBOOK = DestinationKind.DESTINATION_KIND_NOTEBOOK
QUERY = DestinationKind.DESTINATION_KIND_QUERY
FILE = DestinationKind.DESTINATION_KIND_FILE
QUERY_JSON = json.dumps(
    {"cells": [{"cell_type": "code", "source": ["SELECT 1;\n"]}, {"cell_type": "code", "source": "SELECT 2;"}]}
)


def _converted(name: str, kind: DestinationKind | None) -> ConvertedFile:
    return ConvertedFile(f"projects/7/files/{name}", f"{CONVERTED}/{name}", kind)


def _folder(**entries: str) -> io.BytesIO:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("converted/", b"")
        for name, content in entries.items():
            archive.writestr(f"converted/{name}", content)
        archive.writestr("manifest.mf", "Manifest-Version: 1.0\n")
    return io.BytesIO(buffer.getvalue())


def _export(content: str, file_type: str | None = None) -> ExportResponse:
    return ExportResponse(content=base64.b64encode(content.encode()).decode(), file_type=file_type)


@pytest.fixture(name="ws")
def fixture_ws() -> WorkspaceClient:
    return create_autospec(WorkspaceClient)


def test_writes_notebooks_queries_and_files_flat_from_one_download(ws, tmp_path: Path) -> None:
    ws.workspace.download.return_value = _folder(
        **{
            "orders.py": "# orders",
            "daily.dbquery.ipynb": QUERY_JSON,
            "lineage.json": "{}",
            ".lakebridge/chunks/orders/0.json": "internal",
        }
    )
    files = [_converted("orders", NOTEBOOK), _converted("daily.dbquery.ipynb", QUERY), _converted("lineage.json", FILE)]

    written, errors = OutputWriter(ws, tmp_path).write(CONVERTED, files)

    assert not errors
    assert written == {
        "projects/7/files/orders": tmp_path / "orders.py",
        "projects/7/files/daily.dbquery.ipynb": tmp_path / "daily.sql",
        "projects/7/files/lineage.json": tmp_path / "lineage.json",
    }
    assert (tmp_path / "orders.py").read_text() == "# orders"
    assert (tmp_path / "daily.sql").read_text() == "SELECT 1;\n\nSELECT 2;\n"
    assert (tmp_path / "lineage.json").read_text() == "{}"
    # Only the converted files are written: not the manifest, folders or the converter's internal files.
    assert sorted(path.name for path in tmp_path.iterdir()) == ["daily.sql", "lineage.json", "orders.py"]
    ws.workspace.download.assert_called_once_with(CONVERTED, format=ExportFormat.AUTO)
    ws.workspace.export.assert_not_called()


def test_exports_every_file_on_its_own_when_the_folder_cant_be_downloaded(ws, tmp_path: Path) -> None:
    ws.workspace.download.side_effect = DatabricksError("Size of archive exceeds 209715200 bytes")
    ws.workspace.export.side_effect = [_export("# orders", "py"), _export(QUERY_JSON)]
    files = [_converted("orders", NOTEBOOK), _converted("daily.dbquery.ipynb", QUERY)]

    written, errors = OutputWriter(ws, tmp_path).write(CONVERTED, files)

    assert not errors
    assert written == {
        "projects/7/files/orders": tmp_path / "orders.py",
        "projects/7/files/daily.dbquery.ipynb": tmp_path / "daily.sql",
    }
    assert [call.args[0] for call in ws.workspace.export.call_args_list] == [
        f"{CONVERTED}/orders",
        f"{CONVERTED}/daily.dbquery.ipynb",
    ]


def test_exports_only_the_file_the_download_doesnt_have(ws, tmp_path: Path) -> None:
    ws.workspace.download.return_value = _folder(**{"orders.py": "# orders"})
    ws.workspace.export.return_value = _export("# customers", "py")
    files = [_converted("orders", NOTEBOOK), _converted("customers", NOTEBOOK)]

    written, errors = OutputWriter(ws, tmp_path).write(CONVERTED, files)

    assert not errors
    assert set(written.values()) == {tmp_path / "orders.py", tmp_path / "customers.py"}
    ws.workspace.export.assert_called_once_with(f"{CONVERTED}/customers", format=ExportFormat.AUTO)


def test_exports_a_notebook_on_its_own_when_two_entries_could_be_it(ws, tmp_path: Path) -> None:
    ws.workspace.download.return_value = _folder(**{"orders.py": "# py", "orders.sql": "-- sql"})
    ws.workspace.export.return_value = _export("# exported", "py")

    written, errors = OutputWriter(ws, tmp_path).write(CONVERTED, [_converted("orders", NOTEBOOK)])

    assert not errors
    assert written == {"projects/7/files/orders": tmp_path / "orders.py"}
    assert (tmp_path / "orders.py").read_text() == "# exported"


def test_matches_a_file_of_unknown_kind_by_the_same_rules(ws, tmp_path: Path) -> None:
    ws.workspace.download.return_value = _folder(**{"orders.py": "# orders", "daily.dbquery.ipynb": QUERY_JSON})
    files = [_converted("orders", None), _converted("daily.dbquery.ipynb", None)]

    written, errors = OutputWriter(ws, tmp_path).write(CONVERTED, files)

    assert not errors
    assert set(written.values()) == {tmp_path / "orders.py", tmp_path / "daily.sql"}


def test_lists_a_file_that_cant_be_exported_and_writes_the_others(ws, tmp_path: Path) -> None:
    ws.workspace.download.return_value = _folder(**{"orders.py": "# orders"})
    ws.workspace.export.side_effect = NotFound("gone")
    files = [_converted("orders", NOTEBOOK), _converted("customers", NOTEBOOK)]

    written, errors = OutputWriter(ws, tmp_path).write(CONVERTED, files)

    assert written == {"projects/7/files/orders": tmp_path / "orders.py"}
    assert errors == [f"Could not write {CONVERTED}/customers locally: gone"]


def test_leaves_no_partial_file_when_writing_fails(ws, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ws.workspace.download.return_value = _folder(**{"orders.py": "# orders"})
    (tmp_path / "orders.py").write_text("# from an earlier run")

    def fail(*_: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(outputs.os, "replace", fail)

    written, errors = OutputWriter(ws, tmp_path).write(CONVERTED, [_converted("orders", NOTEBOOK)])

    assert not written
    assert errors == [f"Could not write {CONVERTED}/orders locally: disk full"]
    assert [path.name for path in tmp_path.iterdir()] == ["orders.py"]
    assert (tmp_path / "orders.py").read_text() == "# from an earlier run"
