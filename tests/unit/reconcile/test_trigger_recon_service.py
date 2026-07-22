import logging
from unittest.mock import create_autospec

from pyspark.errors import PySparkException
from pyspark.sql import SparkSession

from databricks.labs.lakebridge.reconcile.trigger_recon_service import drop_sample_temp_views


def _spark_listing_temp_views(view_names):
    spark = create_autospec(SparkSession)
    spark.sql.return_value.where.return_value.collect.return_value = [{"viewName": name} for name in view_names]
    return spark


def _drop_view_statements(spark):
    return [c.args[0] for c in spark.sql.call_args_list if str(c.args[0]).startswith("DROP VIEW")]


def test_drop_sample_temp_views_drops_only_prefixed(caplog):
    spark = _spark_listing_temp_views(["recon_keys_aaa", "my_user_view", "_sqldf", "recon_keys_bbb"])

    with caplog.at_level(logging.INFO):
        drop_sample_temp_views(spark)

    assert _drop_view_statements(spark) == [
        "DROP VIEW IF EXISTS recon_keys_aaa",
        "DROP VIEW IF EXISTS recon_keys_bbb",
    ]
    assert any("Dropped sampling temp view recon_keys_aaa" in rec.message for rec in caplog.records)


def test_drop_sample_temp_views_no_matches_drops_nothing():
    spark = _spark_listing_temp_views(["my_user_view", "_sqldf"])

    drop_sample_temp_views(spark)

    assert _drop_view_statements(spark) == []


def test_drop_sample_temp_views_swallows_pyspark_exception(caplog):
    spark = create_autospec(SparkSession)
    spark.sql.side_effect = PySparkException("boom")

    with caplog.at_level(logging.ERROR):
        drop_sample_temp_views(spark)  # best-effort: must not raise

    assert any("Cleaning sampling temp views failed" in rec.message for rec in caplog.records)
