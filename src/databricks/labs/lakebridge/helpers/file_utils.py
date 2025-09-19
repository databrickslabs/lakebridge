import contextlib
import logging
import os
from pathlib import Path
from shutil import move, Error
from datetime import datetime
from collections.abc import Generator

logger = logging.getLogger(__name__)


def is_sql_file(file: str | Path) -> bool:
    """
    Checks if the given file is a SQL file.

    :param file: The name of the file to check.
    :return: True if the file is a SQL file (i.e., its extension is either .sql or .ddl), False otherwise.
    """
    file_extension = Path(file).suffix
    return file_extension.lower() in {".sql", ".ddl"}


def is_dbt_project_file(file: Path):
    # it's ok to hardcode the file name here, see https://docs.getdbt.com/reference/dbt_project.yml
    return file.name == "dbt_project.yml"


def make_dir(path: str | Path) -> None:
    """
    Creates a directory at the specified path if it does not already exist.

    :param path: The path where the directory should be created.
    """
    Path(path).mkdir(parents=True, exist_ok=True)


def dir_walk(root: Path):
    """
    Walks the directory tree rooted at the given path, yielding a tuple containing the root directory, a list of
    :param root: Path
    :return: tuple of  root, subdirectory , files
    """
    sub_dirs = [d for d in root.iterdir() if d.is_dir()]
    files = [f for f in root.iterdir() if f.is_file()]
    yield root, sub_dirs, files

    for each_dir in sub_dirs:
        yield from dir_walk(each_dir)


def get_sql_file(input_path: str | Path) -> Generator[Path, None, None]:
    """
    Returns Generator that yields the names of all SQL files in the given directory.
    :param input_path: Path
    :return: List of SQL files
    """
    for _, _, files in dir_walk(Path(input_path)):
        for filename in files:
            if is_sql_file(filename):
                yield filename


@contextlib.contextmanager
def chdir(new_path: Path) -> Generator[None, None, None]:
    saved_path = Path.cwd()
    try:
        os.chdir(new_path)
        yield
    finally:
        os.chdir(saved_path)


def check_path(path: Path | str) -> bool:
    """Validates a path for both existing files and writable files."""
    try:
        path_obj = Path(path) if not isinstance(path, Path) else path

        if path_obj.exists():
            return os.access(path_obj, os.W_OK)

        parent = path_obj.parent
        return parent.exists() and os.access(parent, os.W_OK)

    except OSError as e:
        logger.warning(f"Could not validate path: {path}, error: {e}")
        return False


def move_tmp_file(tmp_path: Path, output_path: Path) -> None:
    """Process file from a temp directory"""
    try:
        move(tmp_path, output_path.parent)
    except (FileExistsError, Error):
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        new_output_path = output_path.parent / timestamp
        new_output_path.mkdir(exist_ok=True)

        move(tmp_path, new_output_path)
    finally:
        tmp_path.parent.rmdir()
        logger.info(f"Results store at {output_path}")
