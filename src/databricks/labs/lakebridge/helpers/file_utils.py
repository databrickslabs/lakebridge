from pathlib import Path
from collections.abc import Generator


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
