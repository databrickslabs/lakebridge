"""Writes a conversion's outputs from the workspace into the local output folder, flat, as the converter wrote them."""

import base64
import binascii
import io
import json
import logging
import os
import tempfile
import zipfile
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import requests
from databricks.sdk import WorkspaceClient
from databricks.sdk.errors import DatabricksError
from databricks.sdk.service.geniemigrate import DestinationKind
from databricks.sdk.service.workspace import ExportFormat

logger = logging.getLogger(__name__)

QUERY_SUFFIX = ".dbquery.ipynb"
"""The suffix of a converted Databricks SQL query's workspace name."""
NOTEBOOK_EXTENSIONS = ("py", "sql", "scala", "r")
"""The extensions an exported notebook gets, by language."""

_DOWNLOAD_ERRORS = (DatabricksError, requests.exceptions.RequestException, TimeoutError, zipfile.BadZipFile)


@dataclass(frozen=True)
class ConvertedFile:
    """A converted file in the workspace."""

    name: str
    """The project file's resource name, `projects/<id>/files/<file_id>`."""
    destination: str
    """The workspace path of the converted file."""
    kind: DestinationKind | None

    @property
    def destination_name(self) -> str:
        return PurePosixPath(self.destination).name


class OutputWriter:
    """Downloads converted files from the workspace and writes them into the output folder, flat.

    The converter writes every output into one workspace folder, named after its source without the extension, and the
    local output mirrors that folder. One download of the folder (format AUTO, the only one that includes Databricks
    SQL queries) covers both targets; a file the download doesn't have, or every file when the folder can't be
    downloaded, is exported on its own. Queries are written as `<name>.sql`, from the notebook cells they export as.
    """

    def __init__(self, ws: WorkspaceClient, output_folder: Path) -> None:
        self._ws = ws
        """A client with a long HTTP timeout: downloading a folder of 2000 outputs can take minutes."""
        self._output_folder = output_folder

    def write(self, converted_folder: str, files: Sequence[ConvertedFile]) -> tuple[dict[str, Path], list[str]]:
        """Writes the outputs; returns the local path written for each project file, and the export errors."""
        written: dict[str, Path] = {}
        errors: list[str] = []
        entries = self._download_folder(converted_folder) if files else {}
        for file in files:
            try:
                local_name, content = _from_entries(file, entries) or self._export_file(file)
                written[file.name] = self._save(local_name, content)
            except (OSError, ValueError, zipfile.BadZipFile) as err:
                errors.append(f"Could not write {file.destination} locally: {err}")
        return written, errors

    def _download_folder(self, converted_folder: str) -> dict[str, bytes]:
        """The folder's top-level entries by name, or nothing when it can't be downloaded in one call."""
        try:
            raw = self._ws.workspace.download(converted_folder, format=ExportFormat.AUTO).read()
            return _top_level_entries(raw)
        except _DOWNLOAD_ERRORS as err:
            logger.info(f"Exporting the converted files one by one: the folder download failed ({err}).")
            return {}

    def _export_file(self, file: ConvertedFile) -> tuple[str, bytes]:
        """Exports one converted file; returns its local name and content."""
        response = self._ws.workspace.export(file.destination, format=ExportFormat.AUTO)
        try:
            content = base64.b64decode(response.content or "")
        except binascii.Error as err:
            raise ValueError(f"the export isn't readable ({err})") from err
        name = file.destination_name
        if _is_query(file):
            return _query_name(name), _query_sql(content)
        if file.kind != DestinationKind.DESTINATION_KIND_FILE and response.file_type in NOTEBOOK_EXTENSIONS:
            return f"{name}.{response.file_type}", content
        return name, content

    def _save(self, local_name: str, content: bytes) -> Path:
        local = self._output_folder / local_name
        write_atomically(local, content)
        return local


def write_atomically(path: Path, content: bytes) -> None:
    """Writes the file through a temporary file in the same folder, so a crash never leaves half a file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(handle, "wb") as out:
            out.write(content)
        os.replace(temporary, path)
    except OSError:
        Path(temporary).unlink(missing_ok=True)
        raise


def _top_level_entries(raw: bytes) -> dict[str, bytes]:
    """A downloaded folder's entries by name. Only `<folder>/<entry>` counts: not folders, the manifest, or the
    converter's internal files, which it keeps in a dot folder."""
    entries = {}
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        for info in archive.infolist():
            name = PurePosixPath(info.filename)
            if not info.is_dir() and len(name.parts) == 2:
                entries[name.name] = archive.read(info)
    return entries


def _from_entries(file: ConvertedFile, entries: dict[str, bytes]) -> tuple[str, bytes] | None:
    """The local name and content for a file from the folder download, or None when there isn't exactly one match."""
    name = file.destination_name
    kind = file.kind
    if kind in {DestinationKind.DESTINATION_KIND_QUERY, None} and name.endswith(QUERY_SUFFIX) and name in entries:
        return _query_name(name), _query_sql(entries[name])
    if kind in {DestinationKind.DESTINATION_KIND_NOTEBOOK, None}:
        candidates = [f"{name}.{extension}" for extension in NOTEBOOK_EXTENSIONS if f"{name}.{extension}" in entries]
        if len(candidates) == 1:
            return candidates[0], entries[candidates[0]]
        if candidates:
            return None
    if kind in {DestinationKind.DESTINATION_KIND_FILE, None} and name in entries:
        return name, entries[name]
    return None


def _is_query(file: ConvertedFile) -> bool:
    if file.kind == DestinationKind.DESTINATION_KIND_QUERY:
        return True
    return file.kind is None and file.destination_name.endswith(QUERY_SUFFIX)


def _query_name(destination_name: str) -> str:
    return f"{destination_name.removesuffix(QUERY_SUFFIX)}.sql"


def _query_sql(notebook_json: bytes) -> bytes:
    """A query's SQL: the code cells of the notebook it exports as, in order."""
    notebook = json.loads(notebook_json)
    cells = [_cell_source(cell) for cell in notebook.get("cells", []) if cell.get("cell_type") == "code"]
    sql = "\n\n".join(cell.rstrip("\n") for cell in cells if cell.strip())
    return f"{sql}\n".encode() if sql else b""


def _cell_source(cell: dict) -> str:
    source = cell.get("source", "")
    return "".join(source) if isinstance(source, Iterable) and not isinstance(source, str) else str(source)
