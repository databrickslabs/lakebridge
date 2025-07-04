from pathlib import Path
from unittest.mock import create_autospec

import pytest
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
        Schema("s_suppkey", "number"),
        Schema("s_name", "varchar"),
        Schema("s_address", "varchar"),
        Schema("s_nationkey", "number"),
        Schema("s_phone", "varchar"),
        Schema("s_acctbal", "number"),
        Schema("s_comment", "varchar"),
    ]

    sch_with_alias = [
        Schema("s_suppkey_t", "number"),
        Schema("s_name", "varchar"),
        Schema("s_address_t", "varchar"),
        Schema("s_nationkey_t", "number"),
        Schema("s_phone_t", "varchar"),
        Schema("s_acctbal_t", "number"),
        Schema("s_comment_t", "varchar"),
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
