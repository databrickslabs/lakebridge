from pathlib import Path
from unittest.mock import create_autospec

import pytest
from pyspark.sql import DataFrame
from pyspark.sql.types import (
    StructType,
    StructField,
    LongType,
    StringType,
    TimestampType,
    IntegerType,
    BooleanType,
    ArrayType,
    MapType,
)
from databricks.sdk import WorkspaceClient
from databricks.sdk.service import iam

from databricks.labs.lakebridge.reconcile.connectors.dialect_utils import DialectUtils
from databricks.labs.lakebridge.reconcile.connectors.models import NormalizedIdentifier
from databricks.labs.lakebridge.reconcile.connectors.data_source import DataSource, MockDataSource
from databricks.labs.lakebridge.reconcile.recon_config import (
    Table,
    JdbcReaderOptions,
    Transformation,
    ColumnThresholds,
    Filters,
    TableThresholds,
    ColumnMapping,
    Schema,
)
from databricks.labs.lakebridge.reconcile.normalize_recon_config_service import NormalizeReconConfigService


@pytest.fixture()
def mock_workspace_client():
    client = create_autospec(WorkspaceClient)
    client.current_user.me = lambda: iam.User(user_name="remorph", groups=[iam.ComplexValue(display="admins")])
    yield client


@pytest.fixture
def column_mapping():
    return [
        ColumnMapping(source_name="s_suppkey", target_name="s_suppkey_t"),
        ColumnMapping(source_name="s_address", target_name="s_address_t"),
        ColumnMapping(source_name="s_nationkey", target_name="s_nationkey_t"),
        ColumnMapping(source_name="s_phone", target_name="s_phone_t"),
        ColumnMapping(source_name="s_acctbal", target_name="s_acctbal_t"),
        ColumnMapping(source_name="s_comment", target_name="s_comment_t"),
    ]


@pytest.fixture
def normalized_column_mapping():
    return [
        ColumnMapping(source_name="`s_suppkey`", target_name="`s_suppkey_t`"),
        ColumnMapping(source_name="`s_address`", target_name="`s_address_t`"),
        ColumnMapping(source_name="`s_nationkey`", target_name="`s_nationkey_t`"),
        ColumnMapping(source_name="`s_phone`", target_name="`s_phone_t`"),
        ColumnMapping(source_name="`s_acctbal`", target_name="`s_acctbal_t`"),
        ColumnMapping(source_name="`s_comment`", target_name="`s_comment_t`"),
    ]


@pytest.fixture
def column_mapping_normalized():
    return [
        ColumnMapping(source_name="`s$suppkey`", target_name="`s$suppkey_t`"),
        ColumnMapping(source_name="`s$address`", target_name="`s$address_t`"),
        ColumnMapping(source_name="`s$nationkey`", target_name="`s$nationkey_t`"),
        ColumnMapping(source_name="`s$phone`", target_name="`s$phone_t`"),
        ColumnMapping(source_name="`s$acctbal`", target_name="`s$acctbal_t`"),
        ColumnMapping(source_name="`s$comment`", target_name="`s$comment_t`"),
    ]


@pytest.fixture
def table_conf_with_opts(column_mapping):
    return Table(
        source_name="supplier",
        target_name="target_supplier",
        jdbc_reader_options=JdbcReaderOptions(
            number_partitions=100, partition_column="s_nationkey", lower_bound="0", upper_bound="100"
        ),
        join_columns=["s_suppkey", "s_nationkey"],
        select_columns=["s_suppkey", "s_name", "s_address", "s_phone", "s_acctbal", "s_nationkey"],
        drop_columns=["s_comment"],
        column_mapping=column_mapping,
        transformations=[
            Transformation(column_name="s_address", source="trim(s_address)", target="trim(s_address_t)"),
            Transformation(column_name="s_phone", source="trim(s_phone)", target="trim(s_phone_t)"),
            Transformation(column_name="s_name", source="trim(s_name)", target="trim(s_name)"),
        ],
        column_thresholds=[
            ColumnThresholds(column_name="s_acctbal", lower_bound="0", upper_bound="100", type="int"),
        ],
        filters=Filters(source="s_name='t' and s_address='a'", target="s_name='t' and s_address_t='a'"),
        table_thresholds=[
            TableThresholds(lower_bound="0", upper_bound="100", model="mismatch"),
        ],
    )


@pytest.fixture
def table_conf_with_opts_normalized(column_mapping_normalized):
    return Table(
        source_name="supplier",
        target_name="target_supplier",
        jdbc_reader_options=JdbcReaderOptions(
            number_partitions=100, partition_column="`s$nationkey`", lower_bound="0", upper_bound="100"
        ),
        join_columns=["`s$suppkey`", "`s$nationkey`"],
        select_columns=["`s$suppkey`", "`s$name`", "`s$address`", "`s$phone`", "`s$acctbal`", "`s$nationkey`"],
        drop_columns=["`s$comment`"],
        column_mapping=column_mapping_normalized,
        transformations=[
            Transformation(column_name="`s$address`", source="trim(`s$address`)", target="trim(`s$address_t`)"),
            Transformation(column_name="`s$phone`", source="trim(`s$phone`)", target="trim(`s$phone_t`)"),
            Transformation(column_name="`s$name`", source="trim(`s$name`)", target="trim(`s$name`)"),
        ],
        column_thresholds=[
            ColumnThresholds(column_name="`s$acctbal`", lower_bound="0", upper_bound="100", type="int"),
        ],
        filters=Filters(source="`s$name`='t' and `s$address`='a'", target="`s$name`='t' and `s$address_t`='a'"),
        table_thresholds=[
            TableThresholds(lower_bound="0", upper_bound="100", model="mismatch"),
        ],
    )


@pytest.fixture
def table_conf():
    def _table_conf(**kwargs):
        return Table(
            source_name="supplier",
            target_name="supplier",
            jdbc_reader_options=kwargs.get('jdbc_reader_options', None),
            join_columns=kwargs.get('join_columns', None),
            select_columns=kwargs.get('select_columns', None),
            drop_columns=kwargs.get('drop_columns', None),
            column_mapping=kwargs.get('column_mapping', None),
            transformations=kwargs.get('transformations', None),
            column_thresholds=kwargs.get('thresholds', None),
            filters=kwargs.get('filters', None),
        )

    return _table_conf


@pytest.fixture
def table_schema():
    sch = [
        schema_fixture_factory("s_suppkey", "number"),
        schema_fixture_factory("s_name", "varchar"),
        schema_fixture_factory("s_address", "varchar"),
        schema_fixture_factory("s_nationkey", "number"),
        schema_fixture_factory("s_phone", "varchar"),
        schema_fixture_factory("s_acctbal", "number"),
        schema_fixture_factory("s_comment", "varchar"),
    ]

    sch_with_alias = [
        schema_fixture_factory("s_suppkey_t", "number"),
        schema_fixture_factory("s_name", "varchar"),
        schema_fixture_factory("s_address_t", "varchar"),
        schema_fixture_factory("s_nationkey_t", "number"),
        schema_fixture_factory("s_phone_t", "varchar"),
        schema_fixture_factory("s_acctbal_t", "number"),
        schema_fixture_factory("s_comment_t", "varchar"),
    ]

    return sch, sch_with_alias


@pytest.fixture
def table_schema_with_special_chars():
    sch = [
        ansi_schema_fixture_factory("s$suppkey", "number"),
        ansi_schema_fixture_factory("s$name", "varchar"),
        ansi_schema_fixture_factory("s$address", "varchar"),
        ansi_schema_fixture_factory("s$nationkey", "number"),
        ansi_schema_fixture_factory("s$phone", "varchar"),
        ansi_schema_fixture_factory("s$acctbal", "number"),
        ansi_schema_fixture_factory("s$comment", "varchar"),
    ]

    sch_with_alias = [
        ansi_schema_fixture_factory("s$suppkey_t", "number"),
        ansi_schema_fixture_factory("s$name", "varchar"),
        ansi_schema_fixture_factory("s$address_t", "varchar"),
        ansi_schema_fixture_factory("s$nationkey_t", "number"),
        ansi_schema_fixture_factory("s$phone_t", "varchar"),
        ansi_schema_fixture_factory("s$acctbal_t", "number"),
        ansi_schema_fixture_factory("s$comment_t", "varchar"),
    ]

    return sch, sch_with_alias


@pytest.fixture
def report_tables_schema():
    recon_schema = StructType(
        [
            StructField("recon_table_id", LongType(), nullable=False),
            StructField("recon_id", StringType(), nullable=False),
            StructField("source_type", StringType(), nullable=False),
            StructField(
                "source_table",
                StructType(
                    [
                        StructField('catalog', StringType(), nullable=False),
                        StructField('schema', StringType(), nullable=False),
                        StructField('table_name', StringType(), nullable=False),
                    ]
                ),
                nullable=False,
            ),
            StructField(
                "target_table",
                StructType(
                    [
                        StructField('catalog', StringType(), nullable=False),
                        StructField('schema', StringType(), nullable=False),
                        StructField('table_name', StringType(), nullable=False),
                    ]
                ),
                nullable=False,
            ),
            StructField("report_type", StringType(), nullable=False),
            StructField("operation_name", StringType(), nullable=False),
            StructField("start_ts", TimestampType()),
            StructField("end_ts", TimestampType()),
        ]
    )

    metrics_schema = StructType(
        [
            StructField("recon_table_id", LongType(), nullable=False),
            StructField(
                "recon_metrics",
                StructType(
                    [
                        StructField(
                            "row_comparison",
                            StructType(
                                [
                                    StructField("missing_in_source", IntegerType()),
                                    StructField("missing_in_target", IntegerType()),
                                ]
                            ),
                        ),
                        StructField(
                            "column_comparison",
                            StructType(
                                [
                                    StructField("absolute_mismatch", IntegerType()),
                                    StructField("threshold_mismatch", IntegerType()),
                                    StructField("mismatch_columns", StringType()),
                                ]
                            ),
                        ),
                        StructField("schema_comparison", BooleanType()),
                    ]
                ),
            ),
            StructField(
                "run_metrics",
                StructType(
                    [
                        StructField("status", BooleanType(), nullable=False),
                        StructField("run_by_user", StringType(), nullable=False),
                        StructField("exception_message", StringType()),
                    ]
                ),
            ),
            StructField("inserted_ts", TimestampType(), nullable=False),
        ]
    )

    details_schema = StructType(
        [
            StructField("recon_table_id", LongType(), nullable=False),
            StructField("recon_type", StringType(), nullable=False),
            StructField("status", BooleanType(), nullable=False),
            StructField("data", ArrayType(MapType(StringType(), StringType())), nullable=False),
            StructField("inserted_ts", TimestampType(), nullable=False),
        ]
    )

    return recon_schema, metrics_schema, details_schema


# TODO remove normalized_ansi and normalized_source
#  and make source delimiter is required so our specs
#  are behaving like production which uses normalization
def schema_fixture_factory(
    column_name: str,
    data_type: str,
    normalized_ansi: str | None = None,
    normalized_source: str | None = None,
    source_delimiter: str | None = None,
) -> Schema:
    normalized_ansi = normalized_ansi if normalized_ansi else column_name
    normalized_source = normalized_source if normalized_source else column_name

    if source_delimiter:
        normalized = DialectUtils.normalize_identifier(column_name, source_delimiter, source_delimiter)
        normalized_ansi = normalized.ansi_normalized
        normalized_source = normalized.source_normalized

    return Schema(normalized_ansi, data_type, normalized_ansi, normalized_source)  # Production uses ansi here


def oracle_schema_fixture_factory(column_name: str, data_type: str) -> Schema:
    norm = DialectUtils.normalize_identifier(column_name, "\"", "\"")
    return schema_fixture_factory(
        norm.ansi_normalized,
        data_type,
        norm.ansi_normalized,
        norm.source_normalized,
    )


def ansi_schema_fixture_factory(column_name: str, data_type: str) -> Schema:
    ansi = DialectUtils.ansi_normalize_identifier(column_name)
    return schema_fixture_factory(
        ansi,
        data_type,
        ansi,
        ansi,
    )


@pytest.fixture
def mock_data_source():
    return MockDataSource({}, {})


@pytest.fixture
def bladebridge_artifact() -> Path:
    artifact = (
        Path(__file__).parent
        / "resources"
        / "transpiler_configs"
        / "bladebridge"
        / "wheel"
        / "databricks_bb_plugin-0.1.9-py3-none-any.whl"
    )
    assert artifact.exists()
    return artifact


@pytest.fixture
def morpheus_artifact() -> Path:
    artifact = (
        Path(__file__).parent
        / "resources"
        / "transpiler_configs"
        / "morpheus"
        / "jar"
        / "databricks-morph-plugin-0.4.0.jar"
    )
    assert artifact.exists()
    return artifact


class FakeDataSource(DataSource):

    def __init__(self, delimiter: str):
        self.delimiter = delimiter

    def get_schema(self, catalog: str | None, schema: str, table: str, normalize: bool = True) -> list[Schema]:
        raise RuntimeError("Not implemented")

    def normalize_identifier(self, identifier: str) -> NormalizedIdentifier:
        return DialectUtils.normalize_identifier(identifier, self.delimiter, self.delimiter)

    def read_data(
        self, catalog: str | None, schema: str, table: str, query: str, options: JdbcReaderOptions | None
    ) -> DataFrame:
        raise RuntimeError("Not implemented")


@pytest.fixture
def fake_oracle_datasource() -> FakeDataSource:
    return FakeDataSource("\"")


@pytest.fixture
def fake_databricks_datasource() -> FakeDataSource:
    return FakeDataSource("`")


@pytest.fixture
def normalize_config_service(fake_databricks_datasource) -> NormalizeReconConfigService:
    return NormalizeReconConfigService(fake_databricks_datasource, fake_databricks_datasource)
    # If the config is not escaped or is ansi, then databricks can be used


@pytest.fixture
def normalized_table_conf_with_opts(normalize_config_service: NormalizeReconConfigService, table_conf_with_opts):
    return normalize_config_service.normalize_recon_table_config(table_conf_with_opts)


@pytest.fixture
def snowflake_table_conf_with_opts(normalize_config_service: NormalizeReconConfigService, table_conf_with_opts):
    conf = normalize_config_service.normalize_recon_table_config(table_conf_with_opts)
    conf.transformations = [  # SQL has to be valid
        Transformation(column_name="`s_address`", source="trim(\"s_address\")", target="trim(`s_address_t`)"),
        Transformation(column_name="`s_phone`", source="trim(\"s_phone\")", target="trim(`s_phone_t`)"),
        Transformation(column_name="`s_name`", source="trim(\"s_name\")", target="trim(`s_name`)"),
    ]
    if conf.filters:
        conf.filters.source = "\"s_name\"='t' and \"s_address\"='a'"
    return conf


@pytest.fixture
def table_schema_oracle_ansi(table_schema):
    src_schema, tgt_schema = table_schema
    src_schema = [oracle_schema_fixture_factory(s.column_name, s.data_type) for s in src_schema]
    tgt_schema = [ansi_schema_fixture_factory(s.column_name, s.data_type) for s in tgt_schema]
    return src_schema, tgt_schema


@pytest.fixture
def table_schema_ansi_ansi(table_schema):
    src_schema, tgt_schema = table_schema
    src_schema = [ansi_schema_fixture_factory(s.column_name, s.data_type) for s in src_schema]
    tgt_schema = [ansi_schema_fixture_factory(s.column_name, s.data_type) for s in tgt_schema]
    return src_schema, tgt_schema
