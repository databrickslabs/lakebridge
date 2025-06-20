import codecs
import re
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


def read_file(filepath: str | Path) -> str:
    """
    Reads the contents of the given file and returns it as a string.
    :param filepath: Input File Path
    :return: File Contents as String
    """
    file_path = Path(filepath) if isinstance(filepath, str) else filepath
    encoding = detect_encoding(file_path)
    return file_path.read_text(encoding)


def detect_encoding(file_path: Path) -> str:
    """
    detects the encoding of the given file and returns it as a string.
    :param file_path: Input File Path
    :return: File Encoding as String
    """
    with open(file_path, "rb") as f:
        # check is starts with BOM
        data = f.read(6)
        if data[0:3] == codecs.BOM_UTF8:
            return "utf-8"
        if data[0:4] == codecs.BOM_UTF32_BE:
            return "utf-32-be"
        if data[0:4] == codecs.BOM_UTF32_LE:
            return "utf-32-le"
        if data[0:2] == codecs.BOM_UTF16_BE:
            return "utf-16-be"
        if data[0:2] == codecs.BOM_UTF16_LE:
            return "utf-16-le"
        if data[0:5].decode("ascii") == "<?xml":
            return _detect_xml_encoding(file_path)
        # assume utf-8 by default
        return "utf-8"


def _detect_xml_encoding(file_path: Path):
    with open(file_path, "r", encoding="ascii") as f:
        line = f.readline()
        match = re.search("<\\?xml( +([a-z]+)=\"([^\"]+)\")* *\\?>", line)
        if match:
            was_encoding = False
            for group in match.groups():
                if was_encoding:
                    return group
                was_encoding = group == "encoding"
        return "utf-8"
