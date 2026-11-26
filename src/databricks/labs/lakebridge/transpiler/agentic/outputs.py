"""Writes a conversion's outputs from the workspace into the local output folder."""

import base64
import io
import json
import logging
import zipfile
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from databricks.sdk import WorkspaceClient
from databricks.sdk.errors import DatabricksError
from databricks.sdk.service.geniemigrate import DestinationKind
from databricks.sdk.service.workspace import ExportFormat

logger = logging.getLogger(__name__)

QUERY_SUFFIX = ".dbquery.ipynb"
"""The suffix of a converted Databricks SQL query's workspace path."""


def workspace_relative(path: str) -> str:
    """A workspace path without the `/Workspace` prefix: responses may leave it out, requests may include it."""
    return path.removeprefix("/Workspace") if path.startswith("/Workspace/") else path


@dataclass(frozen=True)
class ConvertedFile:
    """A converted file in the workspace, and the source it was converted from."""

    source: PurePosixPath
    """The source file, relative to the input folder."""
    destination: str
    """The workspace path of the converted file."""
    kind: DestinationKind | None

    @property
    def destination_name(self) -> str:
        return PurePosixPath(self.destination).name


class OutputWriter:
    """Exports converted files from the workspace and writes each next to its source's place in the output folder.

    The converter writes every output flat into one workspace folder. Notebooks and plain files are exported with that
    folder in one call; a file the folder export doesn't have, or every file when the folder can't be exported in one
    call, is exported on its own. Databricks SQL queries aren't in the folder export: each is exported on its own, as
    notebook JSON whose cells hold the SQL, and written as `<name>.sql`.
    """

    def __init__(self, ws: WorkspaceClient, output_folder: Path) -> None:
        self._ws = ws
        self._output_folder = output_folder

    def write(
        self, converted_folder: str, files: Sequence[ConvertedFile]
    ) -> tuple[dict[PurePosixPath, Path], list[str]]:
        """Writes the outputs; returns the local path written for each source, and the export errors."""
        written: dict[PurePosixPath, Path] = {}
        errors: list[str] = []
        exported = self._export_folder(
            converted_folder, [f for f in files if f.kind != DestinationKind.DESTINATION_KIND_QUERY]
        )
        for file in files:
            try:
                local = self._write_one(file, exported)
            except (OSError, ValueError, zipfile.BadZipFile) as err:
                errors.append(f"{file.source}: could not export {file.destination}: {err}")
                continue
            if local is not None:
                written[file.source] = local
        return written, errors

    def _write_one(self, file: ConvertedFile, exported: dict[str, bytes]) -> Path | None:
        if file.kind == DestinationKind.DESTINATION_KIND_QUERY:
            name = file.destination_name.removesuffix(QUERY_SUFFIX)
            return self._save(file.source, f"{name}.sql", self._export_query(file.destination))
        entry = _find_entry(file.destination_name, exported)
        if entry is not None:
            return self._save(file.source, entry, exported[entry])
        content, name = self._export_file(file)
        return self._save(file.source, name, content)

    def _export_folder(self, converted_folder: str, files: Sequence[ConvertedFile]) -> dict[str, bytes]:
        """The folder's notebooks and files by exported name, or nothing when it can't be exported in one call."""
        if not files:
            return {}
        try:
            response = self._ws.workspace.export(converted_folder, format=ExportFormat.SOURCE)
        except DatabricksError as err:
            logger.info(f"Exporting the converted files one by one: the folder export failed ({err}).")
            return {}
        exported = {}
        with zipfile.ZipFile(io.BytesIO(base64.b64decode(response.content or ""))) as archive:
            for info in archive.infolist():
                name = PurePosixPath(info.filename)
                # Entries are `<folder>/<file>`; skip folders, the manifest, and anything nested deeper.
                if info.is_dir() or len(name.parts) != 2 or name.name == "manifest.mf":
                    continue
                exported[name.name] = archive.read(info)
        return exported

    def _export_file(self, file: ConvertedFile) -> tuple[bytes, str]:
        response = self._ws.workspace.export(file.destination, format=ExportFormat.SOURCE)
        content = base64.b64decode(response.content or "")
        name = file.destination_name
        if file.kind == DestinationKind.DESTINATION_KIND_NOTEBOOK and response.file_type:
            name = f"{name}.{response.file_type}"
        return content, name

    def _export_query(self, destination: str) -> bytes:
        response = self._ws.workspace.export(destination, format=ExportFormat.SOURCE)
        notebook = json.loads(base64.b64decode(response.content or ""))
        cells = [_cell_source(cell) for cell in notebook.get("cells", []) if cell.get("cell_type") == "code"]
        sql = "\n\n".join(cell.rstrip("\n") for cell in cells if cell.strip())
        return f"{sql}\n".encode() if sql else b""

    def _save(self, source: PurePosixPath, name: str, content: bytes) -> Path:
        local = self._output_folder / Path(*source.parent.parts) / name
        local.parent.mkdir(parents=True, exist_ok=True)
        local.write_bytes(content)
        return local


def _find_entry(destination_name: str, exported: dict[str, bytes]) -> str | None:
    """The exported entry for a destination: the same name, or the name plus the extension the export adds."""
    if destination_name in exported:
        return destination_name
    for entry in exported:
        if PurePosixPath(entry).stem == destination_name:
            return entry
    return None


def _cell_source(cell: dict) -> str:
    source = cell.get("source", "")
    return "".join(source) if isinstance(source, Iterable) and not isinstance(source, str) else str(source)
