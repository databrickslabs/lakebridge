from pathlib import Path
import datetime
import json

import pytest
from pyspark.sql import Row, SparkSession
from pyspark.sql.functions import countDistinct
from pyspark.sql.types import BooleanType, StringType, StructField, StructType

from databricks.labs.lakebridge.config import DatabaseConfig, ReconcileMetadataConfig
from databricks.labs.lakebridge.transpiler.sqlglot.dialect_utils import get_dialect
from databricks.labs.lakebridge.reconcile.exception import WriteToTableException, ReadAndWriteWithVolumeException
from databricks.labs.lakebridge.reconcile.recon_capture import (
    ReconCapture,
    generate_final_reconcile_output,
    ReconIntermediatePersist,
)
from databricks.labs.lakebridge.reconcile.recon_output_config import (
    DataReconcileOutput,
    MismatchOutput,
    ReconcileOutput,
    ReconcileProcessDuration,
    ReconcileTableOutput,
    SchemaReconcileOutput,
    StatusOutput,
    ThresholdOutput,
    ReconcileRecordCount,
)
from databricks.labs.lakebridge.reconcile.recon_config import (
    Table,
    TableThresholds,
    TableThresholdBoundsException,
)


def data_prep(spark: SparkSession):
    # Mismatch DataFrame
    data = [
        Row(id=1, name_source='source1', name_target='target1', name_match='match1'),
        Row(id=2, name_source='source2', name_target='target2', name_match='match2'),
    ]
    mismatch_df = spark.createDataFrame(data)

    # Missing DataFrames
    data1 = [Row(id=1, name='name1'), Row(id=2, name='name2'), Row(id=3, name='name3')]
    data2 = [Row(id=1, name='name1'), Row(id=2, name='name2'), Row(id=3, name='name3'), Row(id=4, name='name4')]
    df1 = spark.createDataFrame(data1)
    df2 = spark.createDataFrame(data2)

    # Schema Compare Dataframe
    schema = StructType(
        [
            StructField("source_column", StringType(), True),
            StructField("source_datatype", StringType(), True),
            StructField("databricks_column", StringType(), True),
            StructField("databricks_datatype", StringType(), True),
            StructField("is_valid", BooleanType(), True),
        ]
    )

    data = [
        Row(
            source_column="source_column1",
            source_datatype="source_datatype1",
            databricks_column="databricks_column1",
            databricks_datatype="databricks_datatype1",
            is_valid=True,
        ),
        Row(
            source_column="source_column2",
            source_datatype="source_datatype2",
            databricks_column="databricks_column2",
            databricks_datatype="databricks_datatype2",
            is_valid=True,
        ),
        Row(
            source_column="source_column3",
            source_datatype="source_datatype3",
            databricks_column="databricks_column3",
            databricks_datatype="databricks_datatype3",
            is_valid=True,
        ),
        Row(
            source_column="source_column4",
            source_datatype="source_datatype4",
            databricks_column="databricks_column4",
            databricks_datatype="databricks_datatype4",
            is_valid=True,
        ),
    ]

    schema_df = spark.createDataFrame(data, schema)

    data_rows = [
        Row(id=1, sal_source=1000, sal_target=1100, sal_match=True),
        Row(id=2, sal_source=2000, sal_target=2100, sal_match=False),
    ]
    threshold_df = spark.createDataFrame(data_rows)

    # Prepare output dataclasses
    mismatch = MismatchOutput(mismatch_df=mismatch_df, mismatch_columns=["name"])
    threshold = ThresholdOutput(threshold_df, threshold_mismatch_count=2)
    reconcile_output = DataReconcileOutput(
        mismatch_count=2,
        missing_in_src_count=3,
        missing_in_tgt_count=4,
        mismatch=mismatch,
        missing_in_src=df1,
        missing_in_tgt=df2,
        threshold_output=threshold,
    )
    schema_output = SchemaReconcileOutput(is_valid=True, compare_df=schema_df)
    table_conf = Table(source_name="supplier", target_name="target_supplier")
    reconcile_process = ReconcileProcessDuration(
        start_ts=str(datetime.datetime.now()), end_ts=str(datetime.datetime.now())
    )

    # Drop old data
    spark.sql("DROP TABLE IF EXISTS DEFAULT.main")
    spark.sql("DROP TABLE IF EXISTS DEFAULT.metrics")
    spark.sql("DROP TABLE IF EXISTS DEFAULT.details")

    row_count = ReconcileRecordCount(source=5, target=5)

    return reconcile_output, schema_output, table_conf, reconcile_process, row_count


def test_recon_capture_start_snowflake_all(mock_workspace_client, mock_spark):
    database_config = DatabaseConfig(
        "source_test_schema", "target_test_catalog", "target_test_schema", "source_test_catalog"
    )
    ws = mock_workspace_client
    source_type = get_dialect("snowflake")
    spark = mock_spark
    reconcile_output, schema_output, table_conf, reconcile_process, row_count = data_prep(spark)
    recon_capture = ReconCapture(
        database_config,
        "73b44582-dbb7-489f-bad1-6a7e8f4821b1",
        "all",
        source_type,
        ws,
        spark,
        metadata_config=ReconcileMetadataConfig(schema="default"),
        local_test_run=True,
    )
    recon_capture.start(
        data_reconcile_output=reconcile_output,
        schema_reconcile_output=schema_output,
        table_conf=table_conf,
        recon_process_duration=reconcile_process,
        record_count=row_count,
    )

    # assert main
    remorph_recon_df = spark.sql("select * from DEFAULT.main")
    row = remorph_recon_df.collect()[0]
    assert remorph_recon_df.count() == 1
    assert row.recon_id == "73b44582-dbb7-489f-bad1-6a7e8f4821b1"
    assert row.source_table.catalog == "source_test_catalog"
    assert row.source_table.schema == "source_test_schema"
    assert row.source_table.table_name == "supplier"
    assert row.target_table.catalog == "target_test_catalog"
    assert row.target_table.schema == "target_test_schema"
    assert row.target_table.table_name == "target_supplier"
    assert row.report_type == "all"
    assert row.source_type == "Snowflake"

    # assert metrics
    remorph_recon_metrics_df = spark.sql("select * from DEFAULT.metrics")
    row = remorph_recon_metrics_df.collect()[0]
    assert remorph_recon_metrics_df.count() == 1
    assert row.recon_metrics.row_comparison.missing_in_source == 3
    assert row.recon_metrics.row_comparison.missing_in_target == 4
    assert row.recon_metrics.column_comparison.absolute_mismatch == 2
    assert row.recon_metrics.column_comparison.threshold_mismatch == 2
    assert row.recon_metrics.column_comparison.mismatch_columns == "name"
    assert row.recon_metrics.schema_comparison is True
    assert row.run_metrics.status is False
    assert row.run_metrics.run_by_user == "remorph"
    assert row.run_metrics.exception_message == ""

    # assert details
    remorph_recon_details_df = spark.sql("select * from DEFAULT.details")
    assert remorph_recon_details_df.count() == 5
    assert remorph_recon_details_df.select("recon_type").distinct().count() == 5
    assert (
        remorph_recon_details_df.select("recon_table_id", "status")
        .groupby("recon_table_id")
        .agg(countDistinct("status").alias("count_stat"))
        .collect()[0]
        .count_stat
        == 2
    )
    assert json.dumps(remorph_recon_details_df.where("recon_type = 'mismatch'").select("data").collect()[0].data) == (
        "[{\"id\": \"1\", \"name_source\": \"source1\", \"name_target\": \"target1\", "
        "\"name_match\": \"match1\"}, {\"id\": \"2\", \"name_source\": \"source2\", "
        "\"name_target\": \"target2\", \"name_match\": \"match2\"}]"
    )

    rows = remorph_recon_details_df.orderBy("recon_type").collect()
    assert rows[0].recon_type == "mismatch"
    assert rows[0].status is False
    assert rows[1].recon_type == "missing_in_source"
    assert rows[1].status is False
    assert rows[2].recon_type == "missing_in_target"
    assert rows[2].status is False
    assert rows[3].recon_type == "schema"
    assert rows[3].status is True
    assert rows[4].recon_type == "threshold_mismatch"
    assert rows[4].status is False


def test_test_recon_capture_start_databricks_data(mock_workspace_client, mock_spark):
    database_config = DatabaseConfig("source_test_schema", "target_test_catalog", "target_test_schema")
    ws = mock_workspace_client
    source_type = get_dialect("databricks")
    spark = mock_spark
    recon_capture = ReconCapture(
        database_config,
        "73b44582-dbb7-489f-bad1-6a7e8f4821b1",
        "data",
        source_type,
        ws,
        spark,
        metadata_config=ReconcileMetadataConfig(schema="default"),
        local_test_run=True,
    )
    reconcile_output, schema_output, table_conf, reconcile_process, row_count = data_prep(spark)
    schema_output.compare_df = None

    recon_capture.start(
        data_reconcile_output=reconcile_output,
        schema_reconcile_output=schema_output,
        table_conf=table_conf,
        recon_process_duration=reconcile_process,
        record_count=row_count,
    )

    # assert main
    remorph_recon_df = spark.sql("select * from DEFAULT.main")
    row = remorph_recon_df.collect()[0]
    assert remorph_recon_df.count() == 1
    assert row.source_table.catalog is None
    assert row.report_type == "data"
    assert row.source_type == "Databricks"

    # assert metrics
    remorph_recon_metrics_df = spark.sql("select * from DEFAULT.metrics")
    row = remorph_recon_metrics_df.collect()[0]
    assert row.recon_metrics.schema_comparison is None
    assert row.run_metrics.status is False

    # assert details
    remorph_recon_details_df = spark.sql("select * from DEFAULT.details")
    assert remorph_recon_details_df.count() == 4
    assert remorph_recon_details_df.select("recon_type").distinct().count() == 4


def test_test_recon_capture_start_databricks_row(mock_workspace_client, mock_spark):
    database_config = DatabaseConfig(
        "source_test_schema", "target_test_catalog", "target_test_schema", "source_test_catalog"
    )
    ws = mock_workspace_client
    source_type = get_dialect("databricks")
    spark = mock_spark
    recon_capture = ReconCapture(
        database_config,
        "73b44582-dbb7-489f-bad1-6a7e8f4821b1",
        "row",
        source_type,
        ws,
        spark,
        metadata_config=ReconcileMetadataConfig(schema="default"),
        local_test_run=True,
    )
    reconcile_output, schema_output, table_conf, reconcile_process, row_count = data_prep(spark)
    reconcile_output.mismatch_count = 0
    reconcile_output.mismatch = MismatchOutput()
    reconcile_output.threshold_output = ThresholdOutput()
    schema_output.compare_df = None

    recon_capture.start(
        data_reconcile_output=reconcile_output,
        schema_reconcile_output=schema_output,
        table_conf=table_conf,
        recon_process_duration=reconcile_process,
        record_count=row_count,
    )

    # assert main
    remorph_recon_df = spark.sql("select * from DEFAULT.main")
    row = remorph_recon_df.collect()[0]
    assert remorph_recon_df.count() == 1
    assert row.report_type == "row"
    assert row.source_type == "Databricks"

    # assert metrics
    remorph_recon_metrics_df = spark.sql("select * from DEFAULT.metrics")
    row = remorph_recon_metrics_df.collect()[0]
    assert row.recon_metrics.column_comparison is None
    assert row.recon_metrics.schema_comparison is None
    assert row.run_metrics.status is False

    # assert details
    remorph_recon_details_df = spark.sql("select * from DEFAULT.details")
    assert remorph_recon_details_df.count() == 2
    assert remorph_recon_details_df.select("recon_type").distinct().count() == 2


def test_recon_capture_start_oracle_schema(mock_workspace_client, mock_spark):
    database_config = DatabaseConfig(
        "source_test_schema", "target_test_catalog", "target_test_schema", "source_test_catalog"
    )
    ws = mock_workspace_client
    source_type = get_dialect("oracle")
    spark = mock_spark
    recon_capture = ReconCapture(
        database_config,
        "73b44582-dbb7-489f-bad1-6a7e8f4821b1",
        "schema",
        source_type,
        ws,
        spark,
        metadata_config=ReconcileMetadataConfig(schema="default"),
        local_test_run=True,
    )
    reconcile_output, schema_output, table_conf, reconcile_process, row_count = data_prep(spark)
    reconcile_output.threshold_output = ThresholdOutput()
    reconcile_output.mismatch_count = 0
    reconcile_output.mismatch = MismatchOutput()
    reconcile_output.missing_in_src_count = 0
    reconcile_output.missing_in_tgt_count = 0

    recon_capture.start(
        data_reconcile_output=reconcile_output,
        schema_reconcile_output=schema_output,
        table_conf=table_conf,
        recon_process_duration=reconcile_process,
        record_count=row_count,
    )

    # assert main
    remorph_recon_df = spark.sql("select * from DEFAULT.main")
    row = remorph_recon_df.collect()[0]
    assert remorph_recon_df.count() == 1
    assert row.report_type == "schema"
    assert row.source_type == "Oracle"

    # assert metrics
    remorph_recon_metrics_df = spark.sql("select * from DEFAULT.metrics")
    row = remorph_recon_metrics_df.collect()[0]
    assert row.recon_metrics.row_comparison is None
    assert row.recon_metrics.column_comparison is None
    assert row.recon_metrics.schema_comparison is True
    assert row.run_metrics.status is True

    # assert details
    remorph_recon_details_df = spark.sql("select * from DEFAULT.details")
    assert remorph_recon_details_df.count() == 1
    assert remorph_recon_details_df.select("recon_type").distinct().count() == 1


def test_recon_capture_start_oracle_with_exception(mock_workspace_client, mock_spark):
    database_config = DatabaseConfig(
        "source_test_schema", "target_test_catalog", "target_test_schema", "source_test_catalog"
    )
    ws = mock_workspace_client
    source_type = get_dialect("oracle")
    spark = mock_spark
    recon_capture = ReconCapture(
        database_config,
        "73b44582-dbb7-489f-bad1-6a7e8f4821b1",
        "all",
        source_type,
        ws,
        spark,
        metadata_config=ReconcileMetadataConfig(schema="default"),
        local_test_run=True,
    )
    reconcile_output, schema_output, table_conf, reconcile_process, row_count = data_prep(spark)
    reconcile_output.threshold_output = ThresholdOutput()
    reconcile_output.mismatch_count = 0
    reconcile_output.mismatch = MismatchOutput()
    reconcile_output.missing_in_src_count = 0
    reconcile_output.missing_in_tgt_count = 0
    reconcile_output.exception = "Test exception"

    recon_capture.start(
        data_reconcile_output=reconcile_output,
        schema_reconcile_output=schema_output,
        table_conf=table_conf,
        recon_process_duration=reconcile_process,
        record_count=row_count,
    )

    # assert main
    remorph_recon_df = spark.sql("select * from DEFAULT.main")
    row = remorph_recon_df.collect()[0]
    assert remorph_recon_df.count() == 1
    assert row.report_type == "all"
    assert row.source_type == "Oracle"

    # assert metrics
    remorph_recon_metrics_df = spark.sql("select * from DEFAULT.metrics")
    row = remorph_recon_metrics_df.collect()[0]
    assert row.recon_metrics.schema_comparison is None
    assert row.run_metrics.status is False
    assert row.run_metrics.exception_message == "Test exception"


def test_recon_capture_start_with_exception(mock_workspace_client, mock_spark):
    database_config = DatabaseConfig(
        "source_test_schema", "target_test_catalog", "target_test_schema", "source_test_catalog"
    )
    ws = mock_workspace_client
    source_type = get_dialect("snowflake")
    spark = mock_spark
    recon_capture = ReconCapture(
        database_config,
        "73b44582-dbb7-489f-bad1-6a7e8f4821b1",
        "all",
        source_type,
        ws,
        spark,
    )
    reconcile_output, schema_output, table_conf, reconcile_process, row_count = data_prep(spark)
    with pytest.raises(WriteToTableException):
        recon_capture.start(
            data_reconcile_output=reconcile_output,
            schema_reconcile_output=schema_output,
            table_conf=table_conf,
            recon_process_duration=reconcile_process,
            record_count=row_count,
        )


def test_generate_final_reconcile_output_row(mock_workspace_client, mock_spark):
    database_config = DatabaseConfig(
        "source_test_schema",
        "target_test_catalog",
        "target_test_schema",
    )
    ws = mock_workspace_client
    source_type = get_dialect("databricks")
    spark = mock_spark
    recon_capture = ReconCapture(
        database_config,
        "73b44582-dbb7-489f-bad1-6a7e8f4821b1",
        "row",
        source_type,
        ws,
        spark,
        metadata_config=ReconcileMetadataConfig(schema="default"),
        local_test_run=True,
    )
    reconcile_output, schema_output, table_conf, reconcile_process, row_count = data_prep(spark)
    recon_capture.start(
        data_reconcile_output=reconcile_output,
        schema_reconcile_output=schema_output,
        table_conf=table_conf,
        recon_process_duration=reconcile_process,
        record_count=row_count,
    )

    final_output = generate_final_reconcile_output(
        "73b44582-dbb7-489f-bad1-6a7e8f4821b1",
        mock_spark,
        metadata_config=ReconcileMetadataConfig(schema="default"),
        local_test_run=True,
    )

    assert final_output == ReconcileOutput(
        recon_id='73b44582-dbb7-489f-bad1-6a7e8f4821b1',
        results=[
            ReconcileTableOutput(
                target_table_name='target_test_catalog.target_test_schema.target_supplier',
                source_table_name='source_test_schema.supplier',
                status=StatusOutput(row=False, column=None, schema=None),
                exception_message='',
            )
        ],
    )


def test_generate_final_reconcile_output_data(mock_workspace_client, mock_spark):
    database_config = DatabaseConfig(
        "source_test_schema",
        "target_test_catalog",
        "target_test_schema",
    )
    ws = mock_workspace_client
    source_type = get_dialect("databricks")
    spark = mock_spark
    recon_capture = ReconCapture(
        database_config,
        "73b44582-dbb7-489f-bad1-6a7e8f4821b1",
        "data",
        source_type,
        ws,
        spark,
        metadata_config=ReconcileMetadataConfig(schema="default"),
        local_test_run=True,
    )
    reconcile_output, schema_output, table_conf, reconcile_process, row_count = data_prep(spark)
    recon_capture.start(
        data_reconcile_output=reconcile_output,
        schema_reconcile_output=schema_output,
        table_conf=table_conf,
        recon_process_duration=reconcile_process,
        record_count=row_count,
    )

    final_output = generate_final_reconcile_output(
        "73b44582-dbb7-489f-bad1-6a7e8f4821b1",
        mock_spark,
        metadata_config=ReconcileMetadataConfig(schema="default"),
        local_test_run=True,
    )

    assert final_output == ReconcileOutput(
        recon_id='73b44582-dbb7-489f-bad1-6a7e8f4821b1',
        results=[
            ReconcileTableOutput(
                target_table_name='target_test_catalog.target_test_schema.target_supplier',
                source_table_name='source_test_schema.supplier',
                status=StatusOutput(row=False, column=False, schema=None),
                exception_message='',
            )
        ],
    )


def test_generate_final_reconcile_output_schema(mock_workspace_client, mock_spark):
    database_config = DatabaseConfig(
        "source_test_schema",
        "target_test_catalog",
        "target_test_schema",
    )
    ws = mock_workspace_client
    source_type = get_dialect("databricks")
    spark = mock_spark
    recon_capture = ReconCapture(
        database_config,
        "73b44582-dbb7-489f-bad1-6a7e8f4821b1",
        "schema",
        source_type,
        ws,
        spark,
        metadata_config=ReconcileMetadataConfig(schema="default"),
        local_test_run=True,
    )
    reconcile_output, schema_output, table_conf, reconcile_process, row_count = data_prep(spark)
    recon_capture.start(
        data_reconcile_output=reconcile_output,
        schema_reconcile_output=schema_output,
        table_conf=table_conf,
        recon_process_duration=reconcile_process,
        record_count=row_count,
    )

    final_output = generate_final_reconcile_output(
        "73b44582-dbb7-489f-bad1-6a7e8f4821b1",
        mock_spark,
        metadata_config=ReconcileMetadataConfig(schema="default"),
        local_test_run=True,
    )

    assert final_output == ReconcileOutput(
        recon_id='73b44582-dbb7-489f-bad1-6a7e8f4821b1',
        results=[
            ReconcileTableOutput(
                target_table_name='target_test_catalog.target_test_schema.target_supplier',
                source_table_name='source_test_schema.supplier',
                status=StatusOutput(row=None, column=None, schema=True),
                exception_message='',
            )
        ],
    )


def test_generate_final_reconcile_output_all(mock_workspace_client, mock_spark):
    database_config = DatabaseConfig(
        "source_test_schema",
        "target_test_catalog",
        "target_test_schema",
    )
    ws = mock_workspace_client
    source_type = get_dialect("databricks")
    spark = mock_spark
    recon_capture = ReconCapture(
        database_config,
        "73b44582-dbb7-489f-bad1-6a7e8f4821b1",
        "all",
        source_type,
        ws,
        spark,
        metadata_config=ReconcileMetadataConfig(schema="default"),
        local_test_run=True,
    )
    reconcile_output, schema_output, table_conf, reconcile_process, row_count = data_prep(spark)

    recon_capture.start(
        data_reconcile_output=reconcile_output,
        schema_reconcile_output=schema_output,
        table_conf=table_conf,
        recon_process_duration=reconcile_process,
        record_count=row_count,
    )

    final_output = generate_final_reconcile_output(
        "73b44582-dbb7-489f-bad1-6a7e8f4821b1",
        mock_spark,
        metadata_config=ReconcileMetadataConfig(schema="default"),
        local_test_run=True,
    )

    assert final_output == ReconcileOutput(
        recon_id='73b44582-dbb7-489f-bad1-6a7e8f4821b1',
        results=[
            ReconcileTableOutput(
                target_table_name='target_test_catalog.target_test_schema.target_supplier',
                source_table_name='source_test_schema.supplier',
                status=StatusOutput(row=False, column=False, schema=True),
                exception_message='',
            )
        ],
    )


def test_generate_final_reconcile_output_exception(mock_workspace_client, mock_spark):
    database_config = DatabaseConfig(
        "source_test_schema",
        "target_test_catalog",
        "target_test_schema",
    )
    ws = mock_workspace_client
    source_type = get_dialect("databricks")
    spark = mock_spark
    recon_capture = ReconCapture(
        database_config,
        "73b44582-dbb7-489f-bad1-6a7e8f4821b1",
        "all",
        source_type,
        ws,
        spark,
        metadata_config=ReconcileMetadataConfig(schema="default"),
        local_test_run=True,
    )
    reconcile_output, schema_output, table_conf, reconcile_process, row_count = data_prep(spark)
    reconcile_output.exception = "Test exception"

    recon_capture.start(
        data_reconcile_output=reconcile_output,
        schema_reconcile_output=schema_output,
        table_conf=table_conf,
        recon_process_duration=reconcile_process,
        record_count=row_count,
    )

    final_output = generate_final_reconcile_output(
        "73b44582-dbb7-489f-bad1-6a7e8f4821b1",
        mock_spark,
        metadata_config=ReconcileMetadataConfig(schema="default"),
        local_test_run=True,
    )

    assert final_output == ReconcileOutput(
        recon_id='73b44582-dbb7-489f-bad1-6a7e8f4821b1',
        results=[
            ReconcileTableOutput(
                target_table_name='target_test_catalog.target_test_schema.target_supplier',
                source_table_name='source_test_schema.supplier',
                status=StatusOutput(row=None, column=None, schema=None),
                exception_message='Test exception',
            )
        ],
    )


def test_write_and_read_unmatched_df_with_volumes_with_exception(tmp_path: Path, mock_spark, mock_workspace_client):
    data = [Row(id=1, name='John', sal=5000), Row(id=2, name='Jane', sal=6000), Row(id=3, name='Doe', sal=7000)]
    df = mock_spark.createDataFrame(data)

    path = str(tmp_path)
    df = ReconIntermediatePersist(mock_spark, path).write_and_read_unmatched_df_with_volumes(df)
    assert df.count() == 3

    path = "/path/that/does/not/exist"
    with pytest.raises(ReadAndWriteWithVolumeException):
        ReconIntermediatePersist(mock_spark, path).write_and_read_unmatched_df_with_volumes(df)


def test_clean_unmatched_df_from_volume_with_exception(mock_spark):
    path = "/path/that/does/not/exist"
    with pytest.raises(Exception):
        ReconIntermediatePersist(mock_spark, path).clean_unmatched_df_from_volume()


def test_apply_threshold_for_mismatch_with_true_absolute(mock_workspace_client, mock_spark):
    database_config = DatabaseConfig(
        "source_test_schema", "target_test_catalog", "target_test_schema", "source_test_catalog"
    )
    ws = mock_workspace_client
    source_type = get_dialect("snowflake")
    spark = mock_spark
    reconcile_output, schema_output, table_conf, reconcile_process, row_count = data_prep(spark)
    reconcile_output.missing_in_src_count = 0
    reconcile_output.missing_in_tgt_count = 0
    reconcile_output.missing_in_src = None
    reconcile_output.missing_in_tgt = None
    table_conf.table_thresholds = [
        TableThresholds(lower_bound="0", upper_bound="4", model="mismatch"),
    ]
    recon_capture = ReconCapture(
        database_config,
        "73b44582-dbb7-489f-bad1-6a7e8f4821b1",
        "all",
        source_type,
        ws,
        spark,
        metadata_config=ReconcileMetadataConfig(schema="default"),
        local_test_run=True,
    )
    recon_capture.start(
        data_reconcile_output=reconcile_output,
        schema_reconcile_output=schema_output,
        table_conf=table_conf,
        recon_process_duration=reconcile_process,
        record_count=row_count,
    )

    # assert metrics
    remorph_recon_metrics_df = spark.sql("select * from DEFAULT.metrics")
    row = remorph_recon_metrics_df.collect()[0]
    assert row.run_metrics.status is True


def test_apply_threshold_for_mismatch_with_missing(mock_workspace_client, mock_spark):
    database_config = DatabaseConfig(
        "source_test_schema", "target_test_catalog", "target_test_schema", "source_test_catalog"
    )
    ws = mock_workspace_client
    source_type = get_dialect("snowflake")
    spark = mock_spark
    reconcile_output, schema_output, table_conf, reconcile_process, row_count = data_prep(spark)
    table_conf.table_thresholds = [
        TableThresholds(lower_bound="0", upper_bound="4", model="mismatch"),
    ]
    recon_capture = ReconCapture(
        database_config,
        "73b44582-dbb7-489f-bad1-6a7e8f4821b1",
        "all",
        source_type,
        ws,
        spark,
        metadata_config=ReconcileMetadataConfig(schema="default"),
        local_test_run=True,
    )

    recon_capture.start(
        data_reconcile_output=reconcile_output,
        schema_reconcile_output=schema_output,
        table_conf=table_conf,
        recon_process_duration=reconcile_process,
        record_count=row_count,
    )
    # assert metrics
    remorph_recon_metrics_df = spark.sql("select * from DEFAULT.metrics")
    row = remorph_recon_metrics_df.collect()[0]
    assert row.run_metrics.status is False


def test_apply_threshold_for_mismatch_with_schema_fail(mock_workspace_client, mock_spark):
    database_config = DatabaseConfig(
        "source_test_schema", "target_test_catalog", "target_test_schema", "source_test_catalog"
    )
    ws = mock_workspace_client
    source_type = get_dialect("snowflake")
    spark = mock_spark
    reconcile_output, schema_output, table_conf, reconcile_process, row_count = data_prep(spark)
    table_conf.table_thresholds = [
        TableThresholds(lower_bound="0", upper_bound="4", model="mismatch"),
    ]
    recon_capture = ReconCapture(
        database_config,
        "73b44582-dbb7-489f-bad1-6a7e8f4821b1",
        "all",
        source_type,
        ws,
        spark,
        metadata_config=ReconcileMetadataConfig(schema="default"),
        local_test_run=True,
    )

    reconcile_output.missing_in_src_count = 0
    reconcile_output.missing_in_tgt_count = 0
    schema_output = SchemaReconcileOutput(is_valid=False, compare_df=None)

    recon_capture.start(
        data_reconcile_output=reconcile_output,
        schema_reconcile_output=schema_output,
        table_conf=table_conf,
        recon_process_duration=reconcile_process,
        record_count=row_count,
    )
    # assert metrics
    remorph_recon_metrics_df = spark.sql("select * from DEFAULT.metrics")
    row = remorph_recon_metrics_df.collect()[0]
    assert row.run_metrics.status is False


def test_apply_threshold_for_mismatch_with_wrong_absolute_bound(mock_workspace_client, mock_spark):
    database_config = DatabaseConfig(
        "source_test_schema", "target_test_catalog", "target_test_schema", "source_test_catalog"
    )
    ws = mock_workspace_client
    source_type = get_dialect("snowflake")
    spark = mock_spark
    reconcile_output, schema_output, table_conf, reconcile_process, row_count = data_prep(spark)
    table_conf.table_thresholds = [
        TableThresholds(lower_bound="0", upper_bound="1", model="mismatch"),
    ]
    reconcile_output.missing_in_src_count = 0
    reconcile_output.missing_in_tgt_count = 0
    reconcile_output.threshold_output = ThresholdOutput()
    reconcile_output.missing_in_src = None
    reconcile_output.missing_in_tgt = None
    recon_capture = ReconCapture(
        database_config,
        "73b44582-dbb7-489f-bad1-6a7e8f4821b1",
        "all",
        source_type,
        ws,
        spark,
        metadata_config=ReconcileMetadataConfig(schema="default"),
        local_test_run=True,
    )
    recon_capture.start(
        data_reconcile_output=reconcile_output,
        schema_reconcile_output=schema_output,
        table_conf=table_conf,
        recon_process_duration=reconcile_process,
        record_count=row_count,
    )

    # assert metrics
    remorph_recon_metrics_df = spark.sql("select * from DEFAULT.metrics")
    row = remorph_recon_metrics_df.collect()[0]
    assert row.run_metrics.status is False


def test_apply_threshold_for_mismatch_with_wrong_percentage_bound(mock_workspace_client, mock_spark):
    database_config = DatabaseConfig(
        "source_test_schema", "target_test_catalog", "target_test_schema", "source_test_catalog"
    )
    ws = mock_workspace_client
    source_type = get_dialect("snowflake")
    spark = mock_spark
    reconcile_output, schema_output, table_conf, reconcile_process, row_count = data_prep(spark)
    table_conf.table_thresholds = [
        TableThresholds(lower_bound="0%", upper_bound="20%", model="mismatch"),
    ]
    reconcile_output.missing_in_src_count = 0
    reconcile_output.missing_in_tgt_count = 0
    reconcile_output.threshold_output = ThresholdOutput()
    reconcile_output.missing_in_src = None
    reconcile_output.missing_in_tgt = None
    recon_capture = ReconCapture(
        database_config,
        "73b44582-dbb7-489f-bad1-6a7e8f4821b1",
        "all",
        source_type,
        ws,
        spark,
        metadata_config=ReconcileMetadataConfig(schema="default"),
        local_test_run=True,
    )
    recon_capture.start(
        data_reconcile_output=reconcile_output,
        schema_reconcile_output=schema_output,
        table_conf=table_conf,
        recon_process_duration=reconcile_process,
        record_count=row_count,
    )

    # assert metrics
    remorph_recon_metrics_df = spark.sql("select * from DEFAULT.metrics")
    row = remorph_recon_metrics_df.collect()[0]
    assert row.run_metrics.status is False


def test_apply_threshold_for_mismatch_with_true_percentage_bound(mock_workspace_client, mock_spark):
    database_config = DatabaseConfig(
        "source_test_schema", "target_test_catalog", "target_test_schema", "source_test_catalog"
    )
    ws = mock_workspace_client
    source_type = get_dialect("snowflake")
    spark = mock_spark
    reconcile_output, schema_output, table_conf, reconcile_process, row_count = data_prep(spark)
    table_conf.table_thresholds = [
        TableThresholds(lower_bound="0%", upper_bound="90%", model="mismatch"),
    ]
    reconcile_output.missing_in_src_count = 0
    reconcile_output.missing_in_tgt_count = 0
    reconcile_output.missing_in_src = None
    reconcile_output.missing_in_tgt = None
    recon_capture = ReconCapture(
        database_config,
        "73b44582-dbb7-489f-bad1-6a7e8f4821b1",
        "all",
        source_type,
        ws,
        spark,
        metadata_config=ReconcileMetadataConfig(schema="default"),
        local_test_run=True,
    )
    recon_capture.start(
        data_reconcile_output=reconcile_output,
        schema_reconcile_output=schema_output,
        table_conf=table_conf,
        recon_process_duration=reconcile_process,
        record_count=row_count,
    )

    # assert metrics
    remorph_recon_metrics_df = spark.sql("select * from DEFAULT.metrics")
    row = remorph_recon_metrics_df.collect()[0]
    assert row.run_metrics.status is True


def test_apply_threshold_for_mismatch_with_invalid_bounds(mock_workspace_client, mock_spark):
    database_config = DatabaseConfig(
        "source_test_schema", "target_test_catalog", "target_test_schema", "source_test_catalog"
    )
    ws = mock_workspace_client
    source_type = get_dialect("snowflake")
    spark = mock_spark
    reconcile_output, schema_output, table_conf, reconcile_process, row_count = data_prep(spark)
    reconcile_output.missing_in_src_count = 0
    reconcile_output.missing_in_tgt_count = 0
    reconcile_output.threshold_output = ThresholdOutput()
    reconcile_output.missing_in_src = None
    reconcile_output.missing_in_tgt = None
    recon_capture = ReconCapture(
        database_config,
        "73b44582-dbb7-489f-bad1-6a7e8f4821b1",
        "all",
        source_type,
        ws,
        spark,
        metadata_config=ReconcileMetadataConfig(schema="default"),
        local_test_run=True,
    )
    with pytest.raises(TableThresholdBoundsException):
        table_conf.table_thresholds = [
            TableThresholds(lower_bound="-0%", upper_bound="-40%", model="mismatch"),
        ]
        recon_capture.start(
            data_reconcile_output=reconcile_output,
            schema_reconcile_output=schema_output,
            table_conf=table_conf,
            recon_process_duration=reconcile_process,
            record_count=row_count,
        )

    with pytest.raises(TableThresholdBoundsException):
        table_conf.table_thresholds = [
            TableThresholds(lower_bound="10%", upper_bound="5%", model="mismatch"),
        ]
        recon_capture.start(
            data_reconcile_output=reconcile_output,
            schema_reconcile_output=schema_output,
            table_conf=table_conf,
            recon_process_duration=reconcile_process,
            record_count=row_count,
        )


def test_apply_threshold_for_only_threshold_mismatch_with_true_absolute(mock_workspace_client, mock_spark):
    database_config = DatabaseConfig(
        "source_test_schema", "target_test_catalog", "target_test_schema", "source_test_catalog"
    )
    ws = mock_workspace_client
    source_type = get_dialect("snowflake")
    spark = mock_spark
    reconcile_output, schema_output, table_conf, reconcile_process, row_count = data_prep(spark)
    reconcile_output.mismatch_count = 0
    reconcile_output.missing_in_src_count = 0
    reconcile_output.missing_in_tgt_count = 0
    reconcile_output.missing_in_src = None
    reconcile_output.missing_in_tgt = None
    table_conf.table_thresholds = [
        TableThresholds(lower_bound="0", upper_bound="2", model="mismatch"),
    ]
    recon_capture = ReconCapture(
        database_config,
        "73b44582-dbb7-489f-bad1-6a7e8f4821b1",
        "all",
        source_type,
        ws,
        spark,
        metadata_config=ReconcileMetadataConfig(schema="default"),
        local_test_run=True,
    )
    recon_capture.start(
        data_reconcile_output=reconcile_output,
        schema_reconcile_output=schema_output,
        table_conf=table_conf,
        recon_process_duration=reconcile_process,
        record_count=row_count,
    )

    # assert metrics
    remorph_recon_metrics_df = spark.sql("select * from DEFAULT.metrics")
    row = remorph_recon_metrics_df.collect()[0]
    assert row.run_metrics.status is True
