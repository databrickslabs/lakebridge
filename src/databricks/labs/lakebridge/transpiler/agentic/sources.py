"""The local source files a conversion uploads, and the archive that uploads them."""

import io
import os
import zipfile
from collections import defaultdict
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

MAX_FILES = 2000
"""The most files one conversion takes: a workspace archive import expands at most 2000 entries."""

EXCLUDED_DIRS = frozenset({"node_modules", "__pycache__"})
"""Dependency folders left out of the upload, never walked. Every dot folder is left out too (`.git`, `.venv`, …)."""

EXCLUDED_FILES = frozenset(
    {
        "Thumbs.db",
        "analyzer-output.xlsx",
        "analyzer-output.json",
        # Left behind when an analyzer run is killed.
        "analyzer-output.xlsx.source_file_names.txt",
    }
)
"""Files left out at any depth, besides every dotfile (`.DS_Store`, …): OS cruft and analyzer outputs."""


_FOLDER_TIME = (1980, 1, 1, 0, 0, 0)
"""The earliest time a zip entry can have."""


class SourceFilesError(ValueError):
    """The input can't be converted as it is: too many files, or files whose outputs would collide."""


@dataclass(frozen=True)
class SourceFiles:
    """The files under the input that the conversion uploads, relative to the input folder."""

    root: Path
    """The input folder, or the folder that holds the input file."""
    relative_paths: tuple[PurePosixPath, ...]
    """Each file to upload, relative to `root`, in a stable order."""

    @classmethod
    def collect(cls, input_source: Path) -> "SourceFiles":
        """The files to upload from `input_source`, a file or a folder; fails on inputs the converter can't take."""
        relative: tuple[PurePosixPath, ...]
        if input_source.is_file():
            root = input_source.parent
            relative = (PurePosixPath(input_source.name),)
        else:
            root = input_source
            relative = tuple(PurePosixPath(path.relative_to(root).as_posix()) for path in _walk(root))
        files = cls(root, relative)
        files._check()
        return files

    def _check(self) -> None:
        if not self.relative_paths:
            raise SourceFilesError(f"No files to convert in: {self.root}")
        if len(self.relative_paths) > MAX_FILES:
            msg = (
                f"The input has {len(self.relative_paths)} files, above the {MAX_FILES}-file limit for one conversion."
            )
            raise SourceFilesError(msg)
        # The converter writes every output into one folder, named after its source without the extension.
        by_stem: dict[str, list[PurePosixPath]] = defaultdict(list)
        for path in self.relative_paths:
            by_stem[path.stem].append(path)
        clashes = [paths for paths in by_stem.values() if len(paths) > 1]
        if clashes:
            described = "; ".join(", ".join(str(path) for path in paths) for paths in clashes)
            msg = (
                "These files have the same name without their extension, so their converted outputs would overwrite "
                f"each other: {described}. Rename or move them, then try again."
            )
            raise SourceFilesError(msg)

    def archive(self, root_name: str) -> bytes:
        """A zip of the files under a single top-level folder `root_name/`, with an entry for every folder.

        The workspace expands a zip imported with format AUTO into a folder; it only creates the subfolders the archive
        has entries for.
        """
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            written_dirs: set[PurePosixPath] = set()
            for path in self.relative_paths:
                for parent in reversed(path.parents[:-1]):
                    if parent not in written_dirs:
                        archive.writestr(_folder_entry(f"{root_name}/{parent}/"), b"")
                        written_dirs.add(parent)
                archive.write(self.root / path, f"{root_name}/{path}")
        return buffer.getvalue()


def _folder_entry(name: str) -> zipfile.ZipInfo:
    """A folder entry with a fixed time, so archiving the same files twice gives the same bytes.

    Named folder entries would get the current time instead, which changes every 2 seconds in a zip.
    """
    entry = zipfile.ZipInfo(name, date_time=_FOLDER_TIME)
    entry.external_attr = 0o40775 << 16 | 0x10  # drwxrwxr-x, and the MS-DOS directory flag, as `writestr` sets them.
    return entry


def _walk(root: Path) -> Iterator[Path]:
    """The files under `root` that get uploaded, in a stable order; excluded and dot folders aren't walked."""
    for directory, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if not d.startswith(".") and d not in EXCLUDED_DIRS)
        for name in sorted(files):
            if name.startswith(".") or name in EXCLUDED_FILES:
                continue
            path = Path(directory) / name
            if path.is_file() and not path.is_symlink():
                yield path
