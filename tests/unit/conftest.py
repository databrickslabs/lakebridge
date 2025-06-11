import io
import shutil
from pathlib import Path
from collections.abc import AsyncGenerator
from collections.abc import Generator
from tempfile import TemporaryDirectory
from unittest.mock import create_autospec

import pytest
import yaml

from databricks.sdk import WorkspaceClient
from databricks.sdk.errors import NotFound

from databricks.labs.lakebridge.config import TranspileConfig
from databricks.labs.lakebridge.helpers.file_utils import make_dir
from databricks.labs.lakebridge.transpiler.lsp_engine import LSPEngine
from databricks.sdk.core import Config


@pytest.fixture(scope="session")
def mock_databricks_config():
    yield create_autospec(Config)


@pytest.fixture()
def transpile_config():
    with TemporaryDirectory() as tmpdirname:
        yield TranspileConfig(
            transpiler_config_path="sqlglot",
            source_dialect="snowflake",
            input_source="input_sql",
            output_folder="output_folder",
            error_file_path=tmpdirname + "/errors.lst",
            sdk_config={"cluster_id": "test_cluster"},
            skip_validation=False,
            catalog_name="catalog",
            schema_name="schema",
        )


def path_to_resource(*args: str) -> str:
    resource_path = Path(__file__).parent.parent / "resources"
    for arg in args:
        resource_path = resource_path / arg
    return str(resource_path)


@pytest.fixture
def mock_workspace_client():
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
def output_folder(tmp_path: Path) -> Generator[Path, None, None]:
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
