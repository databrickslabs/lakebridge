import datetime
from pathlib import Path

from pyspark.sql import Row, SparkSession

from databricks.labs.lakebridge.config import DatabaseConfig, ReconcileMetadataConfig
from databricks.labs.lakebridge.reconcile.recon_capture import (
    ReconCapture,
)
from databricks.labs.lakebridge.reconcile.recon_config import Table
from databricks.labs.lakebridge.reconcile.recon_output_config import (
    ReconcileProcessDuration,
    AggregateQueryOutput,
)
from databricks.labs.lakebridge.reconcile.recon_capture import generate_final_reconcile_aggregate_output
from tests.integration.reconcile.test_aggregates_reconcile import expected_reconcile_output_dict, expected_rule_output
from tests.unit.conftest import get_dialect


def remove_directory_recursively(directory_path):
    path = Path(directory_path)
    if path.is_dir():
        for item in path.iterdir():
            if item.is_dir():
                remove_directory_recursively(item)
            else:
                item.unlink()
        path.rmdir()


def agg_data_prep(spark: SparkSession):
    table_conf = Table(source_name="supplier", target_name="target_supplier")
    reconcile_process_duration = ReconcileProcessDuration(
        start_ts=str(datetime.datetime.now()), end_ts=str(datetime.datetime.now())
    )

    # Prepare output dataclasses
    agg_reconcile_output = [
        AggregateQueryOutput(
            rule=expected_rule_output()["count"], reconcile_output=expected_reconcile_output_dict(spark)["count"]
        ),
        AggregateQueryOutput(
            reconcile_output=expected_reconcile_output_dict(spark)["sum"], rule=expected_rule_output()["sum"]
        ),
    ]

    # Drop old data
    spark.sql("DROP TABLE IF EXISTS DEFAULT.main")
    spark.sql("DROP TABLE IF EXISTS DEFAULT.aggregate_rules")
    spark.sql("DROP TABLE IF EXISTS DEFAULT.aggregate_metrics")
    spark.sql("DROP TABLE IF EXISTS DEFAULT.aggregate_details")

    # Get the warehouse location
    warehouse_location = spark.conf.get("spark.sql.warehouse.dir")

    if warehouse_location and Path(warehouse_location.lstrip('file:')).exists():
        tables = ["main", "aggregate_rules", "aggregate_metrics", "aggregate_details"]
        for table in tables:
            remove_directory_recursively(f"{warehouse_location.lstrip('file:')}/{table}")

    return agg_reconcile_output, table_conf, reconcile_process_duration


def test_aggregates_reconcile_store_aggregate_metrics(mock_workspace_client, mock_spark):
    database_config = DatabaseConfig(
        "source_test_schema", "target_test_catalog", "target_test_schema", "source_test_catalog"
    )

    source_type = get_dialect("snowflake")
    spark = mock_spark
    agg_reconcile_output, table_conf, reconcile_process_duration = agg_data_prep(mock_spark)

    recon_id = "999fygdrs-dbb7-489f-bad1-6a7e8f4821b1"

    recon_capture = ReconCapture(
        database_config,
        recon_id,
        "",
        source_type,
        mock_workspace_client,
        spark,
        metadata_config=ReconcileMetadataConfig(schema="default"),
        local_test_run=True,
    )
    recon_capture.store_aggregates_metrics(table_conf, reconcile_process_duration, agg_reconcile_output)

    # Check if the tables are created

    # assert main table data
    remorph_reconcile_df = spark.sql("select * from DEFAULT.main")

    assert remorph_reconcile_df.count() == 1
    if remorph_reconcile_df.first():
        main = remorph_reconcile_df.first().asDict()
        assert main.get("recon_id") == recon_id
        assert main.get("source_type") == "Snowflake"
        assert not main.get("report_type")
        assert main.get("operation_name") == "aggregates-reconcile"

    # assert rules data
    agg_reconcile_rules_df = spark.sql("select * from DEFAULT.aggregate_rules")

    assert agg_reconcile_rules_df.count() == 2
    assert agg_reconcile_rules_df.select("rule_type").distinct().count() == 1
    if agg_reconcile_rules_df.first():
        rule = agg_reconcile_rules_df.first().asDict()
        assert rule.get("rule_type") == "AGGREGATE"
        assert isinstance(rule.get("rule_info"), dict)
        assert rule["rule_info"].keys() == {"agg_type", "agg_column", "group_by_columns"}

    # assert metrics
    agg_reconcile_metrics_df = spark.sql("select * from DEFAULT.aggregate_metrics")

    assert agg_reconcile_metrics_df.count() == 2
    if agg_reconcile_metrics_df.first():
        metric = agg_reconcile_metrics_df.first().asDict()
        assert isinstance(metric.get("recon_metrics"), Row)
        assert metric.get("recon_metrics").asDict().keys() == {"mismatch", "missing_in_source", "missing_in_target"}

    # assert details
    agg_reconcile_details_df = spark.sql("select * from DEFAULT.aggregate_details")

    assert agg_reconcile_details_df.count() == 6
    assert agg_reconcile_details_df.select("recon_type").distinct().count() == 3
    recon_type_values = {
        row["recon_type"] for row in agg_reconcile_details_df.select("recon_type").distinct().collect()
    }

    assert recon_type_values == {"mismatch", "missing_in_source", "missing_in_target"}

    reconcile_output = generate_final_reconcile_aggregate_output(
        recon_id=recon_id,
        spark=mock_spark,
        metadata_config=ReconcileMetadataConfig(schema="default"),
        local_test_run=True,
    )
    assert len(reconcile_output.results) == 1
    assert not reconcile_output.results[0].exception_message
    assert reconcile_output.results[0].status.aggregate is False
