from datetime import datetime, timezone
import tempfile

import pytest
from pyspark.sql import Row, SparkSession
from pyspark.sql.types import BooleanType, StringType, StructField, StructType

from databricks.labs.lakebridge.config import (
    DatabaseConfig,
    ReconcileMetadataConfig,
    ReconcileConfig,
    SourceConnectionConfig,
    TargetConnectionConfig,
    TableRecon,
)
from databricks.labs.lakebridge.transpiler.sqlglot.dialect_utils import get_dialect
from databricks.labs.lakebridge.reconcile.exception import WriteToTableException
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
    # Mismatch frame uses the production convention: key columns + <col>_base / <col>_compare / <col>_match.
    data = [
        Row(id=1, name_base='source1', name_compare='target1', name_match=False),
        Row(id=2, name_base='source2', name_compare='target2', name_match=False),
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
        start_ts=str(datetime.now(tz=timezone.utc)), end_ts=str(datetime.now(tz=timezone.utc))
    )

    row_count = ReconcileRecordCount(source=5, target=5)

    return reconcile_output, schema_output, table_conf, reconcile_process, row_count


def test_recon_capture_start_snowflake_all(ws, spark, recon_metadata, run_by_user):
    database_config = DatabaseConfig(
        "source_test_catalog", "source_test_schema", "target_test_catalog", "target_test_schema"
    )
    source_type = get_dialect("snowflake")
    reconcile_output, schema_output, table_conf, reconcile_process, row_count = data_prep(spark)
    recon_capture = ReconCapture(
        database_config,
        "73b44582-dbb7-489f-bad1-6a7e8f4821b1",
        "all",
        source_type,
        ws,
        spark,
        metadata_config=recon_metadata,
    )
    recon_capture.start(
        data_reconcile_output=reconcile_output,
        schema_reconcile_output=schema_output,
        table_conf=table_conf,
        recon_process_duration=reconcile_process,
        record_count=row_count,
    )

    # assert main
    remorph_recon_df = spark.sql(f"select * from {recon_metadata.catalog}.{recon_metadata.schema}.main")
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
    remorph_recon_metrics_df = spark.sql(f"select * from {recon_metadata.catalog}.{recon_metadata.schema}.metrics")
    row = remorph_recon_metrics_df.collect()[0]
    assert remorph_recon_metrics_df.count() == 1
    assert row.recon_metrics.source_record_count == 5
    assert row.recon_metrics.target_record_count == 5
    assert row.recon_metrics.row_comparison.missing_in_source == 3
    assert row.recon_metrics.row_comparison.missing_in_target == 4
    assert row.recon_metrics.column_comparison.absolute_mismatch == 2
    assert row.recon_metrics.column_comparison.threshold_mismatch == 2
    assert row.recon_metrics.column_comparison.mismatch_columns == "name"
    assert row.recon_metrics.schema_comparison is True
    assert row.run_metrics.status is False
    assert row.run_metrics.run_by_user == run_by_user
    assert row.run_metrics.exception_message == ""

    # assert details (record-level model; schema comparison now lives in schema_details)
    prefix = f"{recon_metadata.catalog}.{recon_metadata.schema}"
    remorph_recon_details_df = spark.sql(f"select * from {prefix}.details")
    assert remorph_recon_details_df.count() == 4
    recon_types = {row.recon_type for row in remorph_recon_details_df.select("recon_type").distinct().collect()}
    assert recon_types == {"mismatch", "missing_in_source", "missing_in_target", "threshold_mismatch"}

    # the two sampled mismatch records, with their VARIANT row images and differing columns
    mismatch_rows = spark.sql(
        f"SELECT to_json(record_key) AS rk, to_json(source_row) AS sr, to_json(target_row) AS tr, "
        f"mismatch_columns AS mc FROM {prefix}.details WHERE recon_type = 'mismatch' ORDER BY rk"
    ).collect()
    assert [r.rk for r in mismatch_rows] == ['{"id":1}', '{"id":2}']
    assert [r.sr for r in mismatch_rows] == ['{"name":"source1"}', '{"name":"source2"}']
    assert [r.tr for r in mismatch_rows] == ['{"name":"target1"}', '{"name":"target2"}']
    assert all(r.mc == ["name"] for r in mismatch_rows)

    # schema comparison rows
    schema_details_df = spark.sql(f"select * from {prefix}.schema_details")
    assert schema_details_df.count() == 4
    assert schema_details_df.where("is_valid = true").count() == 4


def test_test_recon_capture_start_databricks_data(ws, spark, recon_metadata):
    database_config = DatabaseConfig(
        "source_test_catalog", "source_test_schema", "target_test_catalog", "target_test_schema"
    )
    source_type = get_dialect("databricks")
    recon_capture = ReconCapture(
        database_config,
        "73b44582-dbb7-489f-bad1-6a7e8f4821b1",
        "data",
        source_type,
        ws,
        spark,
        metadata_config=recon_metadata,
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
    remorph_recon_df = spark.sql(f"select * from {recon_metadata.catalog}.{recon_metadata.schema}.main")
    row = remorph_recon_df.collect()[0]
    assert remorph_recon_df.count() == 1
    assert row.source_table.catalog == "source_test_catalog"
    assert row.report_type == "data"
    assert row.source_type == "Databricks"

    # assert metrics
    remorph_recon_metrics_df = spark.sql(f"select * from {recon_metadata.catalog}.{recon_metadata.schema}.metrics")
    row = remorph_recon_metrics_df.collect()[0]
    assert row.recon_metrics.schema_comparison is None
    assert row.run_metrics.status is False

    # assert details
    remorph_recon_details_df = spark.sql(f"select * from {recon_metadata.catalog}.{recon_metadata.schema}.details")
    assert remorph_recon_details_df.count() == 4
    assert remorph_recon_details_df.select("recon_type").distinct().count() == 4


def test_test_recon_capture_start_databricks_row(ws, spark, recon_metadata):
    database_config = DatabaseConfig(
        "source_test_catalog", "source_test_schema", "target_test_catalog", "target_test_schema"
    )
    source_type = get_dialect("databricks")
    recon_capture = ReconCapture(
        database_config,
        "73b44582-dbb7-489f-bad1-6a7e8f4821b1",
        "row",
        source_type,
        ws,
        spark,
        metadata_config=recon_metadata,
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
    remorph_recon_df = spark.sql(f"select * from {recon_metadata.catalog}.{recon_metadata.schema}.main")
    row = remorph_recon_df.collect()[0]
    assert remorph_recon_df.count() == 1
    assert row.report_type == "row"
    assert row.source_type == "Databricks"

    # assert metrics
    remorph_recon_metrics_df = spark.sql(f"select * from {recon_metadata.catalog}.{recon_metadata.schema}.metrics")
    row = remorph_recon_metrics_df.collect()[0]
    assert row.recon_metrics.column_comparison is None
    assert row.recon_metrics.schema_comparison is None
    assert row.run_metrics.status is False

    # assert details
    remorph_recon_details_df = spark.sql(f"select * from {recon_metadata.catalog}.{recon_metadata.schema}.details")
    assert remorph_recon_details_df.count() == 2
    assert remorph_recon_details_df.select("recon_type").distinct().count() == 2


def test_recon_capture_start_oracle_schema(ws, spark, recon_metadata):
    database_config = DatabaseConfig(
        "source_test_catalog", "source_test_schema", "target_test_catalog", "target_test_schema"
    )
    source_type = get_dialect("oracle")
    recon_capture = ReconCapture(
        database_config,
        "73b44582-dbb7-489f-bad1-6a7e8f4821b1",
        "schema",
        source_type,
        ws,
        spark,
        metadata_config=recon_metadata,
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
    remorph_recon_df = spark.sql(f"select * from {recon_metadata.catalog}.{recon_metadata.schema}.main")
    row = remorph_recon_df.collect()[0]
    assert remorph_recon_df.count() == 1
    assert row.report_type == "schema"
    assert row.source_type == "Oracle"

    # assert metrics
    remorph_recon_metrics_df = spark.sql(f"select * from {recon_metadata.catalog}.{recon_metadata.schema}.metrics")
    row = remorph_recon_metrics_df.collect()[0]
    assert row.recon_metrics.row_comparison is None
    assert row.recon_metrics.column_comparison is None
    assert row.recon_metrics.schema_comparison is True
    assert row.run_metrics.status is True

    # assert details: a schema-only run writes no row-level details, only schema_details
    prefix = f"{recon_metadata.catalog}.{recon_metadata.schema}"
    remorph_recon_details_df = spark.sql(f"select * from {prefix}.details")
    assert remorph_recon_details_df.count() == 0
    schema_details_df = spark.sql(f"select * from {prefix}.schema_details")
    assert schema_details_df.count() == 4
    assert schema_details_df.where("is_valid = true").count() == 4


def test_recon_capture_start_oracle_with_exception(ws, spark, recon_metadata):
    database_config = DatabaseConfig(
        "source_test_catalog", "source_test_schema", "target_test_catalog", "target_test_schema"
    )
    source_type = get_dialect("oracle")
    recon_capture = ReconCapture(
        database_config,
        "73b44582-dbb7-489f-bad1-6a7e8f4821b1",
        "all",
        source_type,
        ws,
        spark,
        metadata_config=recon_metadata,
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
    remorph_recon_df = spark.sql(f"select * from {recon_metadata.catalog}.{recon_metadata.schema}.main")
    row = remorph_recon_df.collect()[0]
    assert remorph_recon_df.count() == 1
    assert row.report_type == "all"
    assert row.source_type == "Oracle"

    # assert metrics
    remorph_recon_metrics_df = spark.sql(f"select * from {recon_metadata.catalog}.{recon_metadata.schema}.metrics")
    row = remorph_recon_metrics_df.collect()[0]
    assert row.recon_metrics.schema_comparison is None
    assert row.run_metrics.status is False
    assert row.run_metrics.exception_message == "Test exception"


def test_recon_capture_start_with_exception(ws, spark):
    database_config = DatabaseConfig(
        "source_test_catalog", "source_test_schema", "target_test_catalog", "target_test_schema"
    )
    source_type = get_dialect("snowflake")
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


def test_generate_final_reconcile_output_row(ws, spark, recon_metadata):
    database_config = DatabaseConfig(
        "source_test_catalog",
        "source_test_schema",
        "target_test_catalog",
        "target_test_schema",
    )
    source_type = get_dialect("databricks")
    recon_capture = ReconCapture(
        database_config,
        "73b44582-dbb7-489f-bad1-6a7e8f4821b1",
        "row",
        source_type,
        ws,
        spark,
        metadata_config=recon_metadata,
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
        spark,
        metadata_config=recon_metadata,
    )

    assert final_output == ReconcileOutput(
        recon_id='73b44582-dbb7-489f-bad1-6a7e8f4821b1',
        results=[
            ReconcileTableOutput(
                target_table_name='target_test_catalog.target_test_schema.target_supplier',
                source_table_name='source_test_catalog.source_test_schema.supplier',
                status=StatusOutput(row=False, column=None, schema=None),
                exception_message='',
            )
        ],
    )


def test_generate_final_reconcile_output_data(ws, spark, recon_metadata):
    database_config = DatabaseConfig(
        "source_test_catalog",
        "source_test_schema",
        "target_test_catalog",
        "target_test_schema",
    )
    source_type = get_dialect("databricks")
    recon_capture = ReconCapture(
        database_config,
        "73b44582-dbb7-489f-bad1-6a7e8f4821b1",
        "data",
        source_type,
        ws,
        spark,
        metadata_config=recon_metadata,
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
        spark,
        metadata_config=recon_metadata,
    )

    assert final_output == ReconcileOutput(
        recon_id='73b44582-dbb7-489f-bad1-6a7e8f4821b1',
        results=[
            ReconcileTableOutput(
                target_table_name='target_test_catalog.target_test_schema.target_supplier',
                source_table_name='source_test_catalog.source_test_schema.supplier',
                status=StatusOutput(row=False, column=False, schema=None),
                exception_message='',
            )
        ],
    )


def test_generate_final_reconcile_output_schema(ws, spark, recon_metadata):
    database_config = DatabaseConfig(
        "source_test_catalog",
        "source_test_schema",
        "target_test_catalog",
        "target_test_schema",
    )
    source_type = get_dialect("databricks")
    recon_capture = ReconCapture(
        database_config,
        "73b44582-dbb7-489f-bad1-6a7e8f4821b1",
        "schema",
        source_type,
        ws,
        spark,
        metadata_config=recon_metadata,
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
        spark,
        metadata_config=recon_metadata,
    )

    assert final_output == ReconcileOutput(
        recon_id='73b44582-dbb7-489f-bad1-6a7e8f4821b1',
        results=[
            ReconcileTableOutput(
                target_table_name='target_test_catalog.target_test_schema.target_supplier',
                source_table_name='source_test_catalog.source_test_schema.supplier',
                status=StatusOutput(row=None, column=None, schema=True),
                exception_message='',
            )
        ],
    )


def test_generate_final_reconcile_output_all(ws, spark, recon_metadata):
    database_config = DatabaseConfig(
        "source_test_catalog",
        "source_test_schema",
        "target_test_catalog",
        "target_test_schema",
    )
    source_type = get_dialect("databricks")
    recon_capture = ReconCapture(
        database_config,
        "73b44582-dbb7-489f-bad1-6a7e8f4821b1",
        "all",
        source_type,
        ws,
        spark,
        metadata_config=recon_metadata,
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
        spark,
        metadata_config=recon_metadata,
    )

    assert final_output == ReconcileOutput(
        recon_id='73b44582-dbb7-489f-bad1-6a7e8f4821b1',
        results=[
            ReconcileTableOutput(
                target_table_name='target_test_catalog.target_test_schema.target_supplier',
                source_table_name='source_test_catalog.source_test_schema.supplier',
                status=StatusOutput(row=False, column=False, schema=True),
                exception_message='',
            )
        ],
    )


def test_generate_final_reconcile_output_exception(ws, spark, recon_metadata):
    database_config = DatabaseConfig(
        "source_test_catalog",
        "source_test_schema",
        "target_test_catalog",
        "target_test_schema",
    )
    source_type = get_dialect("databricks")
    recon_capture = ReconCapture(
        database_config,
        "73b44582-dbb7-489f-bad1-6a7e8f4821b1",
        "all",
        source_type,
        ws,
        spark,
        metadata_config=recon_metadata,
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
        spark,
        metadata_config=recon_metadata,
    )

    assert final_output == ReconcileOutput(
        recon_id='73b44582-dbb7-489f-bad1-6a7e8f4821b1',
        results=[
            ReconcileTableOutput(
                target_table_name='target_test_catalog.target_test_schema.target_supplier',
                source_table_name='source_test_catalog.source_test_schema.supplier',
                status=StatusOutput(row=None, column=None, schema=None),
                exception_message='Test exception',
            )
        ],
    )


def test_apply_threshold_for_mismatch_with_true_absolute(ws, spark, recon_metadata):
    database_config = DatabaseConfig(
        "source_test_catalog", "source_test_schema", "target_test_catalog", "target_test_schema"
    )
    source_type = get_dialect("snowflake")
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
        metadata_config=recon_metadata,
    )
    recon_capture.start(
        data_reconcile_output=reconcile_output,
        schema_reconcile_output=schema_output,
        table_conf=table_conf,
        recon_process_duration=reconcile_process,
        record_count=row_count,
    )

    # assert metrics
    remorph_recon_metrics_df = spark.sql(f"select * from {recon_metadata.catalog}.{recon_metadata.schema}.metrics")
    row = remorph_recon_metrics_df.collect()[0]
    assert row.run_metrics.status is True


def test_apply_threshold_for_mismatch_with_missing(ws, spark, recon_metadata):
    database_config = DatabaseConfig(
        "source_test_catalog", "source_test_schema", "target_test_catalog", "target_test_schema"
    )
    source_type = get_dialect("snowflake")
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
        metadata_config=recon_metadata,
    )

    recon_capture.start(
        data_reconcile_output=reconcile_output,
        schema_reconcile_output=schema_output,
        table_conf=table_conf,
        recon_process_duration=reconcile_process,
        record_count=row_count,
    )
    # assert metrics
    remorph_recon_metrics_df = spark.sql(f"select * from {recon_metadata.catalog}.{recon_metadata.schema}.metrics")
    row = remorph_recon_metrics_df.collect()[0]
    assert row.run_metrics.status is False


def test_apply_threshold_for_mismatch_with_schema_fail(ws, spark, recon_metadata):
    database_config = DatabaseConfig(
        "source_test_catalog", "source_test_schema", "target_test_catalog", "target_test_schema"
    )
    source_type = get_dialect("snowflake")
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
        metadata_config=recon_metadata,
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
    remorph_recon_metrics_df = spark.sql(f"select * from {recon_metadata.catalog}.{recon_metadata.schema}.metrics")
    row = remorph_recon_metrics_df.collect()[0]
    assert row.run_metrics.status is False


def test_apply_threshold_for_mismatch_with_wrong_absolute_bound(ws, spark, recon_metadata):
    database_config = DatabaseConfig(
        "source_test_catalog", "source_test_schema", "target_test_catalog", "target_test_schema"
    )
    source_type = get_dialect("snowflake")
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
        metadata_config=recon_metadata,
    )
    recon_capture.start(
        data_reconcile_output=reconcile_output,
        schema_reconcile_output=schema_output,
        table_conf=table_conf,
        recon_process_duration=reconcile_process,
        record_count=row_count,
    )

    # assert metrics
    remorph_recon_metrics_df = spark.sql(f"select * from {recon_metadata.catalog}.{recon_metadata.schema}.metrics")
    row = remorph_recon_metrics_df.collect()[0]
    assert row.run_metrics.status is False


def test_apply_threshold_for_mismatch_with_wrong_percentage_bound(ws, spark, recon_metadata):
    database_config = DatabaseConfig(
        "source_test_catalog", "source_test_schema", "target_test_catalog", "target_test_schema"
    )
    source_type = get_dialect("snowflake")
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
        metadata_config=recon_metadata,
    )
    recon_capture.start(
        data_reconcile_output=reconcile_output,
        schema_reconcile_output=schema_output,
        table_conf=table_conf,
        recon_process_duration=reconcile_process,
        record_count=row_count,
    )

    # assert metrics
    remorph_recon_metrics_df = spark.sql(f"select * from {recon_metadata.catalog}.{recon_metadata.schema}.metrics")
    row = remorph_recon_metrics_df.collect()[0]
    assert row.run_metrics.status is False


def test_apply_threshold_for_mismatch_with_true_percentage_bound(ws, spark, recon_metadata):
    database_config = DatabaseConfig(
        "source_test_catalog", "source_test_schema", "target_test_catalog", "target_test_schema"
    )
    source_type = get_dialect("snowflake")
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
        metadata_config=recon_metadata,
    )
    recon_capture.start(
        data_reconcile_output=reconcile_output,
        schema_reconcile_output=schema_output,
        table_conf=table_conf,
        recon_process_duration=reconcile_process,
        record_count=row_count,
    )

    # assert metrics
    remorph_recon_metrics_df = spark.sql(f"select * from {recon_metadata.catalog}.{recon_metadata.schema}.metrics")
    row = remorph_recon_metrics_df.collect()[0]
    assert row.run_metrics.status is True


def test_apply_threshold_for_mismatch_with_invalid_bounds(ws, spark):
    database_config = DatabaseConfig(
        "source_test_catalog", "source_test_schema", "target_test_catalog", "target_test_schema"
    )
    source_type = get_dialect("snowflake")
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


def test_apply_threshold_for_only_threshold_mismatch_with_true_absolute(ws, spark, recon_metadata):
    database_config = DatabaseConfig(
        "source_test_catalog", "source_test_schema", "target_test_catalog", "target_test_schema"
    )
    source_type = get_dialect("snowflake")
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
        metadata_config=recon_metadata,
    )
    recon_capture.start(
        data_reconcile_output=reconcile_output,
        schema_reconcile_output=schema_output,
        table_conf=table_conf,
        recon_process_duration=reconcile_process,
        record_count=row_count,
    )

    # assert metrics
    remorph_recon_metrics_df = spark.sql(f"select * from {recon_metadata.catalog}.{recon_metadata.schema}.metrics")
    row = remorph_recon_metrics_df.collect()[0]
    assert row.run_metrics.status is True


class ReconIntermediatePersistUnderTest(ReconIntermediatePersist):
    @property
    def is_databricks(self) -> bool:
        return self._is_databricks

    @property
    def format(self):
        return self._format


def test_store_run_context(mock_workspace_client, spark, recon_metadata):
    ws = mock_workspace_client
    recon_capture = ReconCapture(
        DatabaseConfig("source_test_catalog", "source_test_schema", "target_test_catalog", "target_test_schema"),
        "73b44582-dbb7-489f-bad1-6a7e8f4821b1",
        "all",
        get_dialect("snowflake"),
        ws,
        spark,
        metadata_config=recon_metadata,
    )
    reconcile_config = ReconcileConfig(
        report_type="all",
        source=SourceConnectionConfig(
            dialect="snowflake", catalog="source_test_catalog", schema="source_test_schema", uc_connection_name="conn"
        ),
        target=TargetConnectionConfig(catalog="target_test_catalog", schema="target_test_schema"),
        metadata_config=recon_metadata,
    )
    table_recon = TableRecon(
        tables=[Table(source_name="supplier", target_name="target_supplier", join_columns=["s_suppkey"])]
    )

    recon_capture.store_run_context(reconcile_config, table_recon)

    prefix = f"{recon_metadata.catalog}.{recon_metadata.schema}"
    ctx = spark.sql(f"select * from {prefix}.recon_run_context")
    assert ctx.count() == 1
    # config is one VARIANT blob; read intent fields schema-on-read
    row = spark.sql(
        f"SELECT recon_id, "
        f"config:reconcile:report_type::string AS report_type, "
        f"config:reconcile:source:dialect::string AS dialect, "
        f"config:table_recon:tables[0]:source_name::string AS source_name "
        f"FROM {prefix}.recon_run_context"
    ).collect()[0]
    assert row.recon_id == "73b44582-dbb7-489f-bad1-6a7e8f4821b1"
    assert row.report_type == "all"
    assert row.dialect == "snowflake"
    assert row.source_name == "supplier"


def test_is_databricks_false(spark):
    conf = ReconcileMetadataConfig()
    persist = ReconIntermediatePersistUnderTest(spark, conf)

    assert persist.is_databricks is False


def test_dir_uses_tempfile(spark):
    conf = ReconcileMetadataConfig()
    persist = ReconIntermediatePersistUnderTest(spark, conf)
    expected = tempfile.gettempdir()

    assert str(persist.base_dir).startswith(expected)


def test_format_uses_parquet(spark):
    conf = ReconcileMetadataConfig()
    persist = ReconIntermediatePersistUnderTest(spark, conf)

    assert persist.format == "parquet"


def test_is_serverless(spark):
    conf = ReconcileMetadataConfig()
    persist = ReconIntermediatePersistUnderTest(spark, conf)

    assert persist.is_serverless is False
