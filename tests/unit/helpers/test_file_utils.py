import codecs
import os
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from databricks.labs.lakebridge.helpers.file_utils import (
    dir_walk,
    is_sql_file,
    make_dir,
    detect_encoding,
)


@pytest.fixture(scope="module")
def setup_module(tmp_path_factory):
    test_dir = tmp_path_factory.mktemp("test_dir")
    sql_file = test_dir / "test.sql"
    sql_file.write_text("SELECT * FROM test;")
    non_sql_file = test_dir / "test.txt"
    non_sql_file.write_text("This is a test.")
    return test_dir, sql_file, non_sql_file


def test_is_sql_file():
    assert is_sql_file("test.sql") is True
    assert is_sql_file("test.ddl") is True
    assert is_sql_file("test.txt") is False
    assert is_sql_file("test") is False


def test_make_dir(tmp_path: Path) -> None:
    new_dir_path = tmp_path / "new_dir"

    # Ensure the directory does not exist
    assert not os.path.exists(new_dir_path)

    # Call the function to create the directory
    make_dir(new_dir_path)

    # Check if the directory now exists
    assert os.path.exists(new_dir_path)


def safe_remove_file(file_path: Path):
    if file_path.exists():
        file_path.unlink()


def safe_remove_dir(dir_path: Path):
    if dir_path.exists():
        dir_path.rmdir()


def test_dir_walk_single_file():
    path = Path("test_dir")
    path.mkdir()
    (path / "test_file.txt").touch()
    result = list(dir_walk(path))
    assert len(result) == 1
    assert result[0][0] == path
    assert len(result[0][1]) == 0
    assert len(result[0][2]) == 1
    safe_remove_file(path / "test_file.txt")
    safe_remove_dir(path)


def test_dir_walk_nested_files():
    path = Path("test_dir")
    path.mkdir()
    (path / "test_file.txt").touch()
    (path / "nested_dir").mkdir()
    (path / "nested_dir" / "nested_file.txt").touch()
    result = list(dir_walk(path))

    assert len(result) == 2
    assert result[0][0] == path
    assert len(result[0][1]) == 1
    assert len(result[0][2]) == 1
    assert result[1][0] == path / "nested_dir"
    assert len(result[1][1]) == 0
    assert len(result[1][2]) == 1
    safe_remove_file(path / "test_file.txt")
    safe_remove_file(path / "nested_dir" / "nested_file.txt")
    safe_remove_dir(path / "nested_dir")
    safe_remove_dir(path)


def test_dir_walk_empty_dir():
    path = Path("empty_dir")
    path.mkdir()
    result = list(dir_walk(path))

    assert len(result) == 1
    assert result[0][0] == path
    assert len(result[0][1]) == 0
    assert len(result[0][2]) == 0
    safe_remove_dir(path)


@pytest.mark.parametrize(
    "encoding,bom",
    [
        ("utf-8", None),
        ("utf-8-sig", codecs.BOM_UTF8),
        ("utf-16-be", codecs.BOM_UTF16_BE),
        ("utf-16-le", codecs.BOM_UTF16_LE),
        ("utf-32-be", codecs.BOM_UTF32_BE),
        ("utf-32-le", codecs.BOM_UTF32_LE),
    ],
)
def test_detects_bom(encoding, bom):
    with TemporaryDirectory() as parent:
        data = "some_text".encode(encoding)
        if bom:
            data = bom + data
        file_path = Path(parent) / "test_file.txt"
        file_path.write_bytes(data)
        expected = "utf-8" if encoding == "utf-8-sig" else encoding
        detected = detect_encoding(file_path)
        assert detected == expected


@pytest.mark.parametrize(
    "encoding",
    [
        "utf-8",
        "iso-8859-1",
    ],
)
def test_detects_xml_encoding(encoding):
    with TemporaryDirectory() as parent:
        text = f"<?xml version=\"1.0\" encoding=\"{encoding}\"?>\n<body/>"
        file_path = Path(parent) / "test_file.xml"
        file_path.write_text(text, "ascii")
        detected = detect_encoding(file_path)
        assert detected == encoding
