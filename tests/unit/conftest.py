import io
import re
import shutil
from pathlib import Path
from collections.abc import AsyncGenerator, Generator, Sequence
from unittest.mock import create_autospec

import pytest
import yaml

from sqlglot import ErrorLevel, UnsupportedError, Dialect, transpile
from sqlglot import parse_one as sqlglot_parse_one
from sqlglot.errors import SqlglotError, ParseError

from databricks.sdk import WorkspaceClient
from databricks.sdk.errors import NotFound

from databricks.labs.lakebridge.config import TranspileConfig
from databricks.labs.lakebridge.helpers.file_utils import make_dir
from databricks.labs.lakebridge.transpiler.lsp.lsp_engine import LSPEngine
from databricks.labs.lakebridge.transpiler.sqlglot.dialect_utils import SQLGLOT_DIALECTS
from databricks.labs.lakebridge.transpiler.sqlglot.generator.databricks import Databricks
from databricks.labs.lakebridge.transpiler.sqlglot.parsers.snowflake import Snowflake
from databricks.sdk.core import Config

from .transpiler.helpers.functional_test_cases import (
    FunctionalTestFile,
    FunctionalTestFileWithExpectedException,
    expected_exceptions,
)


@pytest.fixture(scope="session")
def mock_databricks_config():
    yield create_autospec(Config)


@pytest.fixture()
def transpile_config():
    yield TranspileConfig(
        transpiler_config_path="sqlglot",
        transpiler_options={"-experimental": True},
        source_dialect="snowflake",
        input_source="input_sql",
        output_folder="output_folder",
        sdk_config={"cluster_id": "test_cluster"},
        skip_validation=False,
        catalog_name="catalog",
        schema_name="schema",
    )


# TODO Add Standardized Sql Formatter to python functional tests.
def _normalize_string(value: str) -> str:
    # Remove extra spaces and ensure consistent spacing around parentheses
    value = re.sub(r'\s+', ' ', value)  # Replace multiple spaces with a single space
    value = re.sub(r'\s*\(\s*', ' ( ', value)  # Ensure space around opening parenthesis
    value = re.sub(r'\s*\)\s*', ' ) ', value)  # Ensure space around closing parenthesis
    value = value.strip()  # Remove leading and trailing spaces
    # Remove indentations, trailing spaces from each line, and convert to lowercase
    lines = [line.rstrip() for line in value.splitlines()]
    return " ".join(lines).lower().strip()


@pytest.fixture
def normalize_string():
    return _normalize_string


def get_dialect(input_dialect: str) -> Dialect:
    value = SQLGLOT_DIALECTS.get(input_dialect)
    if isinstance(value, Dialect):
        return value
    if isinstance(value, type(Dialect)):
        return value()
    raise ValueError(f"Can't instantiate dialect from {value}")


def parse_one(sql):
    dialect = Databricks
    return sqlglot_parse_one(sql, read=dialect)


def validate_source_transpile(databricks_sql, *, source=None, pretty=False, experimental=False):
    """
    Validate that:
    1. Everything in `source` transpiles to `databricks_sql`

    Args:
        databricks_sql (str): Main SQL expression
        source (dict): Mapping of dialect -> SQL
        pretty (bool): prettify the output
        experimental (bool): experimental flag False by default
    """

    for source_dialect, source_sql in (source or {}).items():
        write_dialect = get_dialect("experimental") if experimental else get_dialect("databricks")

        actual_sql = "; ".join(
            transpile(
                source_sql,
                read=get_dialect(source_dialect),
                write=write_dialect,
                pretty=pretty,
                error_level=None,
            )
        )
        orig_sql = actual_sql
        actual_sql = _normalize_string(actual_sql.rstrip(';'))
        expected_sql = _normalize_string(databricks_sql.rstrip(';'))

        error_msg = f"""-> *target_sql* `{expected_sql}` is not matching with\
                                \n-> *transpiled_sql* `{actual_sql}`\
                                \n-> for *source_dialect* `{source_dialect}\
        ORIG:
{orig_sql}
                             """

        assert expected_sql == actual_sql, error_msg


def validate_target_transpile(input_sql, *, target=None, pretty=False):
    """
    Validate that:
    1. `target_sql` transpiles to `input_sql` using `target` dialect

    Args:
        input_sql (str): Main SQL expression
        target (dict): Mapping of dialect -> SQL
        pretty (bool): prettify the output
    """
    expression = parse_one(input_sql) if input_sql else None
    for target_dialect, target_sql in (target or {}).items():
        if target_sql is UnsupportedError:
            with pytest.raises(UnsupportedError):
                if expression:
                    expression.sql(target_dialect, unsupported_level=ErrorLevel.RAISE)
        else:
            actual_sql = _normalize_string(
                transpile(
                    target_sql, read=Snowflake, write=get_dialect(target_dialect), pretty=pretty, error_level=None
                )[0]
            )

            expected_sql = _normalize_string(input_sql)

            error_msg = f"""-> *target_sql* `{expected_sql}` is not matching with\
                                \n-> *transpiled_sql* `{actual_sql}`\
                                \n-> for *target_dialect* `{target_dialect}\
                             """

            assert expected_sql == actual_sql, error_msg


@pytest.fixture(scope="session")
def dialect_context():
    yield validate_source_transpile, validate_target_transpile


_ANTLR_CORE_FOLDER = 'core_engine'


def parse_sql_files(input_dir: Path, source: str, target: str, is_expected_exception):
    suite: list[FunctionalTestFile | FunctionalTestFileWithExpectedException] = []
    for filenames in input_dir.rglob("*.sql"):
        # Skip files in the core directory
        if _ANTLR_CORE_FOLDER in filenames.parts:
            continue
        with open(filenames, 'r', encoding="utf-8") as file_content:
            content = file_content.read()
            source_pattern = rf'--\s*{source} sql:\n(.*?)(?=\n--\s*{target} sql:|$)'
            target_pattern = rf'--\s*{target} sql:\n(.*)'

            # Extract source and target queries

            source_match = re.search(source_pattern, content, re.DOTALL)
            target_match = re.search(target_pattern, content, re.DOTALL)

            source_sql = source_match.group(1).strip().rstrip(";") if source_match else ""
            target_sql = target_match.group(1).strip() if target_match else ""

            # when multiple sqls are present below target
            test_name = filenames.name.replace(".sql", "")
            if is_expected_exception:
                exception_type = expected_exceptions.get(test_name, SqlglotError)
                exception = SqlglotError(test_name)
                if exception_type in {ParseError, UnsupportedError}:
                    exception = exception_type(test_name)
                suite.append(
                    FunctionalTestFileWithExpectedException(
                        target_sql,
                        source_sql,
                        test_name,
                        exception,
                        target,
                    )
                )
            else:
                suite.append(FunctionalTestFile(target_sql, source_sql, test_name, target))
    return suite


def get_functional_test_files_from_directory(
    input_dir: Path, source: str, target: str, is_expected_exception=False
) -> Sequence[FunctionalTestFileWithExpectedException]:
    """Get all functional tests in the input_dir."""
    suite = parse_sql_files(input_dir, source, target, is_expected_exception)
    return suite


@pytest.fixture
def expr():
    return parse_one("SELECT col1 FROM DUAL")


def path_to_resource(*args: str) -> str:
    resource_path = Path(__file__).parent.parent / "resources"
    for arg in args:
        resource_path = resource_path / arg
    return str(resource_path)


@pytest.fixture
def mock_workspace_client() -> WorkspaceClient:
    state = {
        "/Users/foo/.lakebridge/config.yml": yaml.dump(
            {
                'version': 3,
                'catalog_name': 'transpiler',
                'schema_name': 'remorph',
                'transpiler_config_path': 'sqlglot',
                'source_dialect': 'snowflake',
                'sdk_config': {'cluster_id': 'test_cluster'},
            }
        ),
        "/Users/foo/.lakebridge/recon_config.yml": yaml.dump(
            {
                'version': 1,
                'source_schema': "src_schema",
                'target_catalog': "src_catalog",
                'target_schema': "tgt_schema",
                'tables': [
                    {
                        "source_name": 'src_table',
                        "target_name": 'tgt_table',
                        "join_columns": ['id'],
                        "jdbc_reader_options": None,
                        "select_columns": None,
                        "drop_columns": None,
                        "column_mapping": None,
                        "transformations": None,
                        "thresholds": None,
                        "filters": None,
                    }
                ],
                'source_catalog': "src_catalog",
            }
        ),
    }

    def download(path: str) -> io.StringIO | io.BytesIO:
        if path not in state:
            raise NotFound(path)
        if ".csv" in path:
            return io.BytesIO(state[path].encode('utf-8'))
        return io.StringIO(state[path])

    workspace_client = create_autospec(WorkspaceClient)
    workspace_client.current_user.me().user_name = "foo"
    workspace_client.workspace.download = download
    config = create_autospec(Config)
    config.warehouse_id = None
    config.cluster_id = None
    workspace_client.config = config
    return workspace_client


def safe_remove_dir(dir_path: Path):
    if dir_path.exists():
        shutil.rmtree(dir_path)


def safe_remove_file(file_path: Path):
    if file_path.exists():
        file_path.unlink()


def write_data_to_file(path: Path, content: str):
    make_dir(path.parent)
    with path.open("w") as writable:
        # added encoding to avoid UnicodeEncodeError while writing to file for token error test
        writable.write(content.encode("utf-8", "ignore").decode("utf-8"))


@pytest.fixture
def input_source(tmp_path: Path) -> Generator[Path, None, None]:
    source_dir = tmp_path / "remorph_source"
    safe_remove_dir(source_dir)  # should never be required but harmless
    make_dir(source_dir)
    # store some files
    input_dir = source_dir / "queries"
    query_1_sql_file = input_dir / "query1.sql"
    query_2_sql_file = input_dir / "query2.sql"
    query_3_sql_file = input_dir / "query3.sql"
    query_4_sql_file = input_dir / "query4.sql"
    query_5_sql_file = input_dir / "query5.sql"
    input_dir = source_dir / "streams"
    stream_1_sql_file = input_dir / "stream1.sql"
    input_dir = source_dir / "schemas"
    call_center_ddl_file = input_dir / "call_center.ddl"
    file_text = source_dir / "file.txt"

    query_1_sql = """select  i_manufact, sum(ss_ext_sales_price) ext_price from date_dim, store_sales where
    d_date_sk = ss_sold_date_sk and substr(ca_zip,1,5) <> substr(s_zip,1,5) group by i_manufact order by i_manufact
    limit 100 ;"""

    query_2_sql = """select wswscs.d_week_seq d_week_seq1,sun_sales sun_sales1,mon_sales mon_sales1 from wswscs,
    date_dim where date_dim.d_week_seq = wswscs.d_week_seq and d_year = 2001"""

    query_3_sql = """with wscs as
     (select sold_date_sk
            ,sales_price
      from (select ws_sold_date_sk sold_date_sk
                  ,ws_ext_sales_price sales_price
            from web_sales
            union all
            select cs_sold_date_sk sold_date_sk
                  ,cs_ext_sales_price sales_price
            from catalog_sales)),
     wswscs as
     (select d_week_seq,
            sum(case when (d_day_name='Sunday') then sales_price else null end) sun_sales,
            sum(case when (d_day_name='Monday') then sales_price else null end) mon_sales,
            sum(case when (d_day_name='Tuesday') then sales_price else  null end) tue_sales,
            sum(case when (d_day_name='Wednesday') then sales_price else null end) wed_sales,
            sum(case when (d_day_name='Thursday') then sales_price else null end) thu_sales,
            sum(case when (d_day_name='Friday') then sales_price else null end) fri_sales,
            sum(case when (d_day_name='Saturday') then sales_price else null end) sat_sales
     from wscs
         ,date_dim
     where d_date_sk = sold_date_sk
     group by d_week_seq)
     select d_week_seq1
           ,round(sun_sales1/sun_sales2,2)
           ,round(mon_sales1/mon_sales2,2)
           ,round(tue_sales1/tue_sales2,2)
           ,round(wed_sales1/wed_sales2,2)
           ,round(thu_sales1/thu_sales2,2)
           ,round(fri_sales1/fri_sales2,2)
           ,round(sat_sales1/sat_sales2,2)
     from
     (select wswscs.d_week_seq d_week_seq1
            ,sun_sales sun_sales1
            ,mon_sales mon_sales1
            ,tue_sales tue_sales1
            ,wed_sales wed_sales1
            ,thu_sales thu_sales1
            ,fri_sales fri_sales1
            ,sat_sales sat_sales1
      from wswscs,date_dim
      where date_dim.d_week_seq = wswscs.d_week_seq and
            d_year = 2001) y,
     (select wswscs.d_week_seq d_week_seq2
            ,sun_sales sun_sales2
            ,mon_sales mon_sales2
            ,tue_sales tue_sales2
            ,wed_sales wed_sales2
            ,thu_sales thu_sales2
            ,fri_sales fri_sales2
            ,sat_sales sat_sales2
      from wswscs
          ,date_dim
      where date_dim.d_week_seq = wswscs.d_week_seq2 and
            d_year = 2001+1) z
     where d_week_seq1=d_week_seq2-53
     order by d_week_seq1;
     """

    query_4_sql = """create table(
    col1 int
    col2 string
    );"""

    query_5_sql = """1SELECT * from ~v\ud83d' table;"""

    stream_1_sql = """CREATE STREAM unsupported_stream AS SELECT * FROM some_table;"""

    call_center_ddl = """create table call_center
        (
            cc_call_center_sk         int                           ,
            cc_call_center_id         varchar(16)
        )

         CLUSTER BY(cc_call_center_sk)
         """

    write_data_to_file(query_1_sql_file, query_1_sql)
    write_data_to_file(query_2_sql_file, query_2_sql)
    write_data_to_file(query_3_sql_file, query_3_sql)
    write_data_to_file(query_4_sql_file, query_4_sql)
    write_data_to_file(query_5_sql_file, query_5_sql)
    write_data_to_file(stream_1_sql_file, stream_1_sql)
    write_data_to_file(call_center_ddl_file, call_center_ddl)
    write_data_to_file(file_text, "This is a test file")
    yield source_dir
    safe_remove_dir(source_dir)


@pytest.fixture
def empty_input_source(tmp_path: Path) -> Generator[Path, None, None]:
    source_dir = tmp_path / "remorph_source"
    source_dir.mkdir()
    yield source_dir
    safe_remove_dir(source_dir)


@pytest.fixture
def output_folder(tmp_path: Path) -> Generator[Path, None, None]:
    # Only the parent of the output folder has to exist.
    output_dir = tmp_path / "remorph_transpiled"
    yield output_dir
    safe_remove_dir(output_dir)


@pytest.fixture
def error_file(tmp_path: Path) -> Generator[Path, None, None]:
    file_path = tmp_path / "transpile_errors.lst"
    yield file_path
    safe_remove_file(file_path)


@pytest.fixture
async def lsp_engine() -> AsyncGenerator[LSPEngine, None]:
    config_path = path_to_resource("lsp_transpiler", "lsp_config.yml")
    engine = LSPEngine.from_config_path(Path(config_path))
    yield engine
    if engine.is_alive:
        await engine.shutdown()
