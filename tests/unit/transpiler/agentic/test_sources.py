import io
import logging
import zipfile
from pathlib import Path, PurePosixPath

import pytest

from databricks.labs.lakebridge.transpiler.agentic import sources
from databricks.labs.lakebridge.transpiler.agentic.sources import SourceFiles, SourceFilesError


def _write(root: Path, *paths: str) -> None:
    for path in paths:
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f"-- {path}\n")


def test_collects_the_files_to_upload_skipping_dot_entries_and_tool_output(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "a.sql",
        "nested/b.sql",
        ".DS_Store",
        ".sqlfluff",
        ".git/config",
        "node_modules/x/y.js",
        "nested/__pycache__/c.pyc",
        "analyzer-output.json",
    )

    files = SourceFiles.collect(tmp_path)

    assert files.root == tmp_path
    assert files.relative_paths == (PurePosixPath("a.sql"), PurePosixPath("nested/b.sql"))


def test_collects_a_single_file(tmp_path: Path) -> None:
    _write(tmp_path, "only.sql", "other.sql")

    files = SourceFiles.collect(tmp_path / "only.sql")

    assert files.root == tmp_path
    assert files.relative_paths == (PurePosixPath("only.sql"),)


def test_rejects_an_input_without_files(tmp_path: Path) -> None:
    _write(tmp_path, ".hidden.sql")

    with pytest.raises(SourceFilesError, match="No files to convert"):
        SourceFiles.collect(tmp_path)


def test_rejects_more_files_than_one_conversion_takes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sources, "MAX_FILES", 2)
    _write(tmp_path, "a.sql", "b.sql", "c.sql")

    with pytest.raises(SourceFilesError, match="has 3 files, above the 2-file limit"):
        SourceFiles.collect(tmp_path)


def test_rejects_files_whose_outputs_would_overwrite_each_other(tmp_path: Path) -> None:
    _write(tmp_path, "a/x.sql", "b/x.ddl", "c/y.sql")

    with pytest.raises(SourceFilesError, match=r"same name without their extension.*a/x\.sql, b/x\.ddl"):
        SourceFiles.collect(tmp_path)


def test_archive_has_every_folder_before_its_files_under_one_root(tmp_path: Path) -> None:
    _write(tmp_path, "a.sql", "nested/deeper/b.sql")

    with zipfile.ZipFile(io.BytesIO(SourceFiles.collect(tmp_path).archive("src"))) as archive:
        names = archive.namelist()
        content = archive.read("src/nested/deeper/b.sql")

    assert names == ["src/a.sql", "src/nested/", "src/nested/deeper/", "src/nested/deeper/b.sql"]
    assert content == b"-- nested/deeper/b.sql\n"


def test_skips_and_logs_symbolic_links(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    root = tmp_path / "input"
    _write(root, "a.sql")
    _write(tmp_path, "outside/b.sql")
    (root / "link.sql").symlink_to(tmp_path / "outside" / "b.sql")
    (root / "linked").symlink_to(tmp_path / "outside", target_is_directory=True)

    with caplog.at_level(logging.WARNING):
        files = SourceFiles.collect(root)

    assert files.relative_paths == (PurePosixPath("a.sql"),)
    assert "Skipping a symbolic link, it isn't uploaded: link.sql" in caplog.text
    assert "Skipping a symbolic link, it isn't uploaded: linked" in caplog.text
