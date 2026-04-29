import dataclasses

from pyspark import Row

from databricks.labs.lakebridge.config import DatabaseConfig
from databricks.labs.lakebridge.reconcile.connectors.data_source import MockDataSource
from databricks.labs.lakebridge.reconcile.reconciliation import Reconciliation
from databricks.labs.lakebridge.reconcile.schema_compare import SchemaCompare
from databricks.labs.lakebridge.transpiler.sqlglot.dialect_utils import get_dialect
from tests.integration.reconcile.conftest import FakeReconIntermediatePersist

CATALOG = "org"
SCHEMA = "data"
SRC_TABLE = "supplier"
TGT_TABLE = "target_supplier"

SOURCE_HASH_QUERY = "SELECT LOWER(SHA2(TRIM(s_address) || TRIM(s_name) || COALESCE(TRIM(`s_nationkey`), '_null_recon_') || TRIM(s_phone) || COALESCE(TRIM(`s_suppkey`), '_null_recon_'), 256)) AS hash_value_recon, `s_nationkey` AS `s_nationkey`, `s_suppkey` AS `s_suppkey` FROM :tbl WHERE s_name = 't' AND s_address = 'a'"
TARGET_HASH_QUERY = "SELECT LOWER(SHA2(TRIM(s_address_t) || TRIM(s_name) || COALESCE(TRIM(`s_nationkey_t`), '_null_recon_') || TRIM(s_phone_t) || COALESCE(TRIM(`s_suppkey_t`), '_null_recon_'), 256)) AS hash_value_recon, `s_nationkey_t` AS `s_nationkey`, `s_suppkey_t` AS `s_suppkey` FROM :tbl WHERE s_name = 't' AND s_address_t = 'a'"
SOURCE_MISMATCH_QUERY = "WITH recon AS (SELECT CAST(22 AS number) AS `s_nationkey`, CAST(2 AS number) AS `s_suppkey`), src AS (SELECT TRIM(s_address) AS `s_address`, TRIM(s_name) AS `s_name`, COALESCE(TRIM(`s_nationkey`), '_null_recon_') AS `s_nationkey`, TRIM(s_phone) AS `s_phone`, COALESCE(TRIM(`s_suppkey`), '_null_recon_') AS `s_suppkey` FROM :tbl WHERE s_name = 't' AND s_address = 'a') SELECT src.`s_address`, src.`s_name`, src.`s_nationkey`, src.`s_phone`, src.`s_suppkey` FROM src INNER JOIN recon AS recon ON src.`s_nationkey` = recon.`s_nationkey` AND src.`s_suppkey` = recon.`s_suppkey`"
TARGET_MISMATCH_QUERY = "WITH recon AS (SELECT 22 AS `s_nationkey`, 2 AS `s_suppkey`), src AS (SELECT TRIM(s_address_t) AS `s_address`, TRIM(s_name) AS `s_name`, COALESCE(TRIM(`s_nationkey_t`), '_null_recon_') AS `s_nationkey`, TRIM(s_phone_t) AS `s_phone`, COALESCE(TRIM(`s_suppkey_t`), '_null_recon_') AS `s_suppkey` FROM :tbl WHERE s_name = 't' AND s_address_t = 'a') SELECT src.`s_address`, src.`s_name`, src.`s_nationkey`, src.`s_phone`, src.`s_suppkey` FROM src INNER JOIN recon AS recon ON src.`s_nationkey` = recon.`s_nationkey` AND src.`s_suppkey` = recon.`s_suppkey`"
SOURCE_THRESHOLD_QUERY = "SELECT `s_nationkey` AS `s_nationkey`, `s_suppkey` AS `s_suppkey`, `s_acctbal` AS `s_acctbal` FROM :tbl WHERE s_name = 't' AND s_address = 'a'"
TARGET_THRESHOLD_QUERY = "SELECT `s_nationkey_t` AS `s_nationkey`, `s_suppkey_t` AS `s_suppkey`, `s_acctbal_t` AS `s_acctbal` FROM :tbl WHERE s_name = 't' AND s_address_t = 'a'"
THRESHOLD_COMPARISON_QUERY = "SELECT COALESCE(source.`s_acctbal`, 0) AS `s_acctbal_source`, COALESCE(databricks.`s_acctbal`, 0) AS `s_acctbal_databricks`, CASE WHEN (COALESCE(source.`s_acctbal`, 0) - COALESCE(databricks.`s_acctbal`, 0)) = 0 THEN 'Match' WHEN (COALESCE(source.`s_acctbal`, 0) - COALESCE(databricks.`s_acctbal`, 0)) BETWEEN 0 AND 100 THEN 'Warning' ELSE 'Failed' END AS `s_acctbal_match`, source.`s_nationkey` AS `s_nationkey_source`, source.`s_suppkey` AS `s_suppkey_source` FROM source_supplier_df_threshold_vw AS source INNER JOIN target_target_supplier_df_threshold_vw AS databricks ON source.`s_nationkey` <=> databricks.`s_nationkey` AND source.`s_suppkey` <=> databricks.`s_suppkey` WHERE (1 = 1 OR 1 = 1) OR (COALESCE(source.`s_acctbal`, 0) - COALESCE(databricks.`s_acctbal`, 0)) <> 0"
TARGET_SAMPLING_QUERY = "SELECT `s_address_t` AS `s_address`, `s_name` AS `s_name`, `s_nationkey_t` AS `s_nationkey`, `s_phone_t` AS `s_phone`, `s_suppkey_t` AS `s_suppkey` FROM :tbl WHERE `s_name` = 't' AND s_address_t = 'a'"


def test_reconcile_data_with_max_sample_size_caps_threshold_samples(
    mock_spark,
    normalized_table_conf_with_opts,
    table_schema_ansi_ansi,
    recon_metadata,
):
    src_schema, tgt_schema = table_schema_ansi_ansi
    table_conf = dataclasses.replace(normalized_table_conf_with_opts, max_sample_size=60)

    src_repo = {
        (CATALOG, SCHEMA, SOURCE_HASH_QUERY): mock_spark.createDataFrame(
            [
                Row(hash_value_recon="a1b", s_nationkey=11, s_suppkey=1),
                Row(hash_value_recon="c2d", s_nationkey=22, s_suppkey=2),
            ]
        ),
        (CATALOG, SCHEMA, SOURCE_MISMATCH_QUERY): mock_spark.createDataFrame(
            [Row(s_address="a-2", s_name="n-2", s_nationkey=22, s_phone="222-2", s_suppkey=2)]
        ),
        (CATALOG, SCHEMA, SOURCE_THRESHOLD_QUERY): mock_spark.createDataFrame(
            [Row(s_nationkey=11, s_suppkey=1, s_acctbal=100)]
        ),
    }
    tgt_repo = {
        (CATALOG, SCHEMA, TARGET_HASH_QUERY): mock_spark.createDataFrame(
            [
                Row(hash_value_recon="a1b", s_nationkey=11, s_suppkey=1),
                Row(hash_value_recon="c2de", s_nationkey=22, s_suppkey=2),
            ]
        ),
        (CATALOG, SCHEMA, TARGET_SAMPLING_QUERY): mock_spark.createDataFrame(
            [Row(hash_value_recon="c2de", s_nationkey=22, s_suppkey=2)]
        ),
        (CATALOG, SCHEMA, TARGET_MISMATCH_QUERY): mock_spark.createDataFrame(
            [Row(s_address="a-22", s_name="n-2", s_nationkey=22, s_phone="222", s_suppkey=2)]
        ),
        (CATALOG, SCHEMA, TARGET_THRESHOLD_QUERY): mock_spark.createDataFrame(
            [Row(s_nationkey=11, s_suppkey=1, s_acctbal=210)]
        ),
        # 100 'Failed' rows; cap to max_sample_size=60.
        (CATALOG, SCHEMA, THRESHOLD_COMPARISON_QUERY): mock_spark.createDataFrame(
            [
                Row(
                    s_acctbal_source=100 + i,
                    s_acctbal_databricks=210 + i,
                    s_acctbal_match="Failed",
                    s_nationkey_source=11,
                    s_suppkey_source=i,
                )
                for i in range(100)
            ]
        ),
    }

    db_config = DatabaseConfig(
        source_catalog=CATALOG, source_schema=SCHEMA, target_catalog=CATALOG, target_schema=SCHEMA
    )
    actual = Reconciliation(
        MockDataSource(src_repo, {(CATALOG, SCHEMA, SRC_TABLE): src_schema}),
        MockDataSource(tgt_repo, {(CATALOG, SCHEMA, TGT_TABLE): tgt_schema}),
        db_config,
        "data",
        SchemaCompare(mock_spark),
        get_dialect("databricks"),
        mock_spark,
        recon_metadata,
        FakeReconIntermediatePersist(),
    ).reconcile_data(table_conf, src_schema, tgt_schema)

    assert actual.threshold_output.threshold_mismatch_count == 100
    assert actual.threshold_output.threshold_df is not None
    assert actual.threshold_output.threshold_df.count() == 60
