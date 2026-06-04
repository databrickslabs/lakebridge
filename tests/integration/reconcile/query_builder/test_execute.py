from pathlib import Path
from dataclasses import dataclass
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock
from uuid import UUID

import pytest
from pyspark import Row
from pyspark.errors import PySparkException
from pyspark.testing import assertDataFrameEqual

from databricks.labs.lakebridge.config import (
    DatabaseConfig,
    TableRecon,
    ReconcileMetadataConfig,
    ReconcileConfig,
    SourceConnectionConfig,
    TargetConnectionConfig,
)
from databricks.labs.lakebridge.reconcile.connectors.databricks import DatabricksDataSource
from databricks.labs.lakebridge.reconcile.connectors.snowflake import SnowflakeDataSource
from databricks.labs.lakebridge.reconcile.reconciliation import Reconciliation
from databricks.labs.lakebridge.reconcile.trigger_recon_service import TriggerReconService
from databricks.labs.lakebridge.reconcile.utils import initialise_data_source
from databricks.labs.lakebridge.transpiler.sqlglot.dialect_utils import get_dialect
from databricks.labs.lakebridge.reconcile.connectors.data_source import MockDataSource
from databricks.labs.lakebridge.reconcile.exception import (
    DataSourceRuntimeException,
    InvalidInputException,
    ReconciliationException,
)
from databricks.labs.lakebridge.reconcile.recon_output_config import (
    DataReconcileOutput,
    MismatchOutput,
    ThresholdOutput,
    ReconcileOutput,
    ReconcileTableOutput,
    StatusOutput,
)
from databricks.labs.lakebridge.reconcile.schema_compare import SchemaCompare
from tests.integration.reconcile.conftest import FakeReconIntermediatePersist

CATALOG = "org"
SCHEMA = "data"
SRC_TABLE = "supplier"
TGT_TABLE = "target_supplier"

MOCK_TIMESTAMP = datetime(2024, 5, 23, 9, 21, 25, 122185, tzinfo=timezone.utc)


@dataclass
class SamplingQueries:
    target_sampling_query: str


@dataclass
class HashQueries:
    source_hash_query: str
    target_hash_query: str


@dataclass
class MismatchQueries:
    source_mismatch_query: str
    target_mismatch_query: str


@dataclass
class MissingQueries:
    source_missing_query: str
    target_missing_query: str


@dataclass
class ThresholdQueries:
    source_threshold_query: str
    target_threshold_query: str
    threshold_comparison_query: str


@dataclass
class RowQueries:
    source_row_query: str
    target_row_query: str


@dataclass
class RecordCountQueries:
    source_record_count_query: str
    target_record_count_query: str


@dataclass
class QueryStore:
    hash_queries: HashQueries
    mismatch_queries: MismatchQueries
    missing_queries: MissingQueries
    threshold_queries: ThresholdQueries
    row_queries: RowQueries
    record_count_queries: RecordCountQueries
    sampling_queries: SamplingQueries


@pytest.fixture
def query_store(spark):
    source_hash_query = "SELECT LOWER(SHA2(TRIM(s_address) || TRIM(s_name) || COALESCE(TRIM(`s_nationkey`), '_null_recon_') || TRIM(s_phone) || COALESCE(TRIM(`s_suppkey`), '_null_recon_'), 256)) AS hash_value_recon, `s_nationkey` AS `s_nationkey`, `s_suppkey` AS `s_suppkey` FROM :tbl WHERE s_name = 't' AND s_address = 'a'"
    target_hash_query = "SELECT LOWER(SHA2(TRIM(s_address_t) || TRIM(s_name) || COALESCE(TRIM(`s_nationkey_t`), '_null_recon_') || TRIM(s_phone_t) || COALESCE(TRIM(`s_suppkey_t`), '_null_recon_'), 256)) AS hash_value_recon, `s_nationkey_t` AS `s_nationkey`, `s_suppkey_t` AS `s_suppkey` FROM :tbl WHERE s_name = 't' AND s_address_t = 'a'"
    source_mismatch_query = "SELECT src.`s_address`, src.`s_name`, src.`s_nationkey`, src.`s_phone`, src.`s_suppkey` FROM (SELECT TRIM(s_address) AS `s_address`, TRIM(s_name) AS `s_name`, COALESCE(TRIM(`s_nationkey`), '_null_recon_') AS `s_nationkey`, TRIM(s_phone) AS `s_phone`, COALESCE(TRIM(`s_suppkey`), '_null_recon_') AS `s_suppkey` FROM :tbl WHERE s_name = 't' AND s_address = 'a') AS src INNER JOIN (SELECT CAST(22 AS number) AS `s_nationkey`, CAST(2 AS number) AS `s_suppkey`) AS recon ON src.`s_nationkey` = recon.`s_nationkey` AND src.`s_suppkey` = recon.`s_suppkey`"
    target_mismatch_query = "SELECT src.`s_address`, src.`s_name`, src.`s_nationkey`, src.`s_phone`, src.`s_suppkey` FROM (SELECT TRIM(s_address_t) AS `s_address`, TRIM(s_name) AS `s_name`, COALESCE(TRIM(`s_nationkey_t`), '_null_recon_') AS `s_nationkey`, TRIM(s_phone_t) AS `s_phone`, COALESCE(TRIM(`s_suppkey_t`), '_null_recon_') AS `s_suppkey` FROM :tbl WHERE s_name = 't' AND s_address_t = 'a') AS src INNER JOIN (SELECT 22 AS `s_nationkey`, 2 AS `s_suppkey`) AS recon ON src.`s_nationkey` = recon.`s_nationkey` AND src.`s_suppkey` = recon.`s_suppkey`"
    source_missing_query = "SELECT src.`s_address`, src.`s_name`, src.`s_nationkey`, src.`s_phone`, src.`s_suppkey` FROM (SELECT TRIM(s_address_t) AS `s_address`, TRIM(s_name) AS `s_name`, COALESCE(TRIM(`s_nationkey_t`), '_null_recon_') AS `s_nationkey`, TRIM(s_phone_t) AS `s_phone`, COALESCE(TRIM(`s_suppkey_t`), '_null_recon_') AS `s_suppkey` FROM :tbl WHERE s_name = 't' AND s_address_t = 'a') AS src INNER JOIN (SELECT 44 AS `s_nationkey`, 4 AS `s_suppkey`) AS recon ON src.`s_nationkey` = recon.`s_nationkey` AND src.`s_suppkey` = recon.`s_suppkey`"
    target_missing_query = "SELECT src.`s_address`, src.`s_name`, src.`s_nationkey`, src.`s_phone`, src.`s_suppkey` FROM (SELECT TRIM(s_address) AS `s_address`, TRIM(s_name) AS `s_name`, COALESCE(TRIM(`s_nationkey`), '_null_recon_') AS `s_nationkey`, TRIM(s_phone) AS `s_phone`, COALESCE(TRIM(`s_suppkey`), '_null_recon_') AS `s_suppkey` FROM :tbl WHERE s_name = 't' AND s_address = 'a') AS src INNER JOIN (SELECT CAST(33 AS number) AS `s_nationkey`, CAST(3 AS number) AS `s_suppkey`) AS recon ON src.`s_nationkey` = recon.`s_nationkey` AND src.`s_suppkey` = recon.`s_suppkey`"
    source_threshold_query = "SELECT `s_nationkey` AS `s_nationkey`, `s_suppkey` AS `s_suppkey`, `s_acctbal` AS `s_acctbal` FROM :tbl WHERE s_name = 't' AND s_address = 'a'"
    target_threshold_query = "SELECT `s_nationkey_t` AS `s_nationkey`, `s_suppkey_t` AS `s_suppkey`, `s_acctbal_t` AS `s_acctbal` FROM :tbl WHERE s_name = 't' AND s_address_t = 'a'"
    threshold_comparison_query = "SELECT COALESCE(source.`s_acctbal`, 0) AS `s_acctbal_source`, COALESCE(databricks.`s_acctbal`, 0) AS `s_acctbal_databricks`, CASE WHEN (COALESCE(source.`s_acctbal`, 0) - COALESCE(databricks.`s_acctbal`, 0)) = 0 THEN 'Match' WHEN (COALESCE(source.`s_acctbal`, 0) - COALESCE(databricks.`s_acctbal`, 0)) BETWEEN 0 AND 100 THEN 'Warning' ELSE 'Failed' END AS `s_acctbal_match`, source.`s_nationkey` AS `s_nationkey_source`, source.`s_suppkey` AS `s_suppkey_source` FROM source_supplier_df_threshold_vw AS source INNER JOIN target_target_supplier_df_threshold_vw AS databricks ON source.`s_nationkey` <=> databricks.`s_nationkey` AND source.`s_suppkey` <=> databricks.`s_suppkey` WHERE (1 = 1 OR 1 = 1) OR (COALESCE(source.`s_acctbal`, 0) - COALESCE(databricks.`s_acctbal`, 0)) <> 0"
    source_row_query = "SELECT LOWER(SHA2(TRIM(s_address) || TRIM(s_name) || COALESCE(TRIM(`s_nationkey`), '_null_recon_') || TRIM(s_phone) || COALESCE(TRIM(`s_suppkey`), '_null_recon_'), 256)) AS hash_value_recon, TRIM(s_address) AS `s_address`, TRIM(s_name) AS `s_name`, `s_nationkey` AS `s_nationkey`, TRIM(s_phone) AS `s_phone`, `s_suppkey` AS `s_suppkey` FROM :tbl WHERE s_name = 't' AND s_address = 'a'"
    target_row_query = "SELECT LOWER(SHA2(TRIM(s_address_t) || TRIM(s_name) || COALESCE(TRIM(`s_nationkey_t`), '_null_recon_') || TRIM(s_phone_t) || COALESCE(TRIM(`s_suppkey_t`), '_null_recon_'), 256)) AS hash_value_recon, TRIM(s_address_t) AS `s_address`, TRIM(s_name) AS `s_name`, `s_nationkey_t` AS `s_nationkey`, TRIM(s_phone_t) AS `s_phone`, `s_suppkey_t` AS `s_suppkey` FROM :tbl WHERE s_name = 't' AND s_address_t = 'a'"
    hash_queries = HashQueries(
        source_hash_query=source_hash_query,
        target_hash_query=target_hash_query,
    )
    mismatch_queries = MismatchQueries(
        source_mismatch_query=source_mismatch_query,
        target_mismatch_query=target_mismatch_query,
    )
    missing_queries = MissingQueries(
        source_missing_query=source_missing_query,
        target_missing_query=target_missing_query,
    )
    threshold_queries = ThresholdQueries(
        source_threshold_query=source_threshold_query,
        target_threshold_query=target_threshold_query,
        threshold_comparison_query=threshold_comparison_query,
    )
    row_queries = RowQueries(
        source_row_query=source_row_query,
        target_row_query=target_row_query,
    )
    record_count_queries = RecordCountQueries(
        source_record_count_query="SELECT COUNT(1) AS record_count FROM :tbl WHERE s_name = 't' AND s_address = 'a'",
        target_record_count_query="SELECT COUNT(1) AS record_count FROM :tbl WHERE s_name = 't' AND s_address_t = 'a'",
    )
    sampling_queries = SamplingQueries(
        target_sampling_query="SELECT TRIM(s_address_t) AS `s_address`, TRIM(s_name) AS `s_name`, COALESCE(TRIM(`s_nationkey_t`), '_null_recon_') AS `s_nationkey`, TRIM(s_phone_t) AS `s_phone`, COALESCE(TRIM(`s_suppkey_t`), '_null_recon_') AS `s_suppkey` FROM :tbl WHERE s_name = 't' AND s_address_t = 'a'"
    )

    return QueryStore(
        hash_queries=hash_queries,
        mismatch_queries=mismatch_queries,
        missing_queries=missing_queries,
        threshold_queries=threshold_queries,
        row_queries=row_queries,
        record_count_queries=record_count_queries,
        sampling_queries=sampling_queries,
    )


def test_reconcile_data_with_mismatches_and_missing(
    spark,
    normalized_table_conf_with_opts,
    table_schema_ansi_ansi,
    query_store,
    tmp_path: Path,
    recon_metadata: ReconcileMetadataConfig,
):
    src_schema, tgt_schema = table_schema_ansi_ansi
    source_dataframe_repository = {
        (
            CATALOG,
            SCHEMA,
            query_store.hash_queries.source_hash_query,
        ): spark.createDataFrame(
            [
                Row(hash_value_recon="a1b", s_nationkey=11, s_suppkey=1),
                Row(hash_value_recon="c2d", s_nationkey=22, s_suppkey=2),
                Row(hash_value_recon="e3g", s_nationkey=33, s_suppkey=3),
            ]
        ),
        (CATALOG, SCHEMA, query_store.mismatch_queries.source_mismatch_query): spark.createDataFrame(
            [Row(s_address="address-2", s_name="name-2", s_nationkey=22, s_phone="222-2", s_suppkey=2)]
        ),
        (CATALOG, SCHEMA, query_store.missing_queries.target_missing_query): spark.createDataFrame(
            [Row(s_address="address-3", s_name="name-3", s_nationkey=33, s_phone="333", s_suppkey=3)]
        ),
        (CATALOG, SCHEMA, query_store.threshold_queries.source_threshold_query): spark.createDataFrame(
            [Row(s_nationkey=11, s_suppkey=1, s_acctbal=100)]
        ),
    }
    source_schema_repository = {(CATALOG, SCHEMA, SRC_TABLE): src_schema}
    target_dataframe_repository = {
        (
            CATALOG,
            SCHEMA,
            query_store.hash_queries.target_hash_query,
        ): spark.createDataFrame(
            [
                Row(hash_value_recon="a1b", s_nationkey=11, s_suppkey=1),
                Row(hash_value_recon="c2de", s_nationkey=22, s_suppkey=2),
                Row(hash_value_recon="k4l", s_nationkey=44, s_suppkey=4),
            ]
        ),
        (
            CATALOG,
            SCHEMA,
            query_store.sampling_queries.target_sampling_query,
        ): spark.createDataFrame(
            [
                Row(hash_value_recon="a1b", s_nationkey=11, s_suppkey=1),
                Row(hash_value_recon="c2de", s_nationkey=22, s_suppkey=2),
                Row(hash_value_recon="k4l", s_nationkey=44, s_suppkey=4),
            ]
        ),
        (CATALOG, SCHEMA, query_store.mismatch_queries.target_mismatch_query): spark.createDataFrame(
            [Row(s_address="address-22", s_name="name-2", s_nationkey=22, s_phone="222", s_suppkey=2)]
        ),
        (CATALOG, SCHEMA, query_store.missing_queries.source_missing_query): spark.createDataFrame(
            [Row(s_address="address-4", s_name="name-4", s_nationkey=44, s_phone="444", s_suppkey=4)]
        ),
        (CATALOG, SCHEMA, query_store.threshold_queries.target_threshold_query): spark.createDataFrame(
            [Row(s_nationkey=11, s_suppkey=1, s_acctbal=210)]
        ),
        (CATALOG, SCHEMA, query_store.threshold_queries.threshold_comparison_query): spark.createDataFrame(
            [
                Row(
                    s_acctbal_source=100,
                    s_acctbal_databricks=210,
                    s_acctbal_match="Failed",
                    s_nationkey_source=11,
                    s_suppkey_source=1,
                )
            ]
        ),
    }
    target_schema_repository = {(CATALOG, SCHEMA, TGT_TABLE): tgt_schema}
    database_config = DatabaseConfig(
        source_catalog=CATALOG,
        source_schema=SCHEMA,
        target_catalog=CATALOG,
        target_schema=SCHEMA,
    )
    schema_comparator = SchemaCompare(spark)
    source = MockDataSource(source_dataframe_repository, source_schema_repository)
    target = MockDataSource(target_dataframe_repository, target_schema_repository)
    actual_data_reconcile = Reconciliation(
        source,
        target,
        database_config,
        "data",
        schema_comparator,
        get_dialect("databricks"),
        spark,
        recon_metadata,
        FakeReconIntermediatePersist(),
    ).reconcile_data(normalized_table_conf_with_opts, src_schema, tgt_schema)
    expected_data_reconcile = DataReconcileOutput(
        mismatch_count=1,
        missing_in_src_count=1,
        missing_in_tgt_count=1,
        missing_in_src=spark.createDataFrame(
            [Row(s_address="address-4", s_name="name-4", s_nationkey=44, s_phone="444", s_suppkey=4)]
        ),
        missing_in_tgt=spark.createDataFrame(
            [Row(s_address="address-3", s_name="name-3", s_nationkey=33, s_phone="333", s_suppkey=3)]
        ),
        mismatch=MismatchOutput(
            mismatch_df=spark.createDataFrame(
                [
                    Row(
                        s_suppkey=2,
                        s_nationkey=22,
                        s_address_base="address-2",
                        s_address_compare="address-22",
                        s_address_match=False,
                        s_name_base="name-2",
                        s_name_compare="name-2",
                        s_name_match=True,
                        s_phone_base="222-2",
                        s_phone_compare="222",
                        s_phone_match=False,
                    )
                ]
            ),
            mismatch_columns=["s_address", "s_phone"],
        ),
        threshold_output=ThresholdOutput(
            threshold_df=spark.createDataFrame(
                [
                    Row(
                        s_acctbal_source=100,
                        s_acctbal_databricks=210,
                        s_acctbal_match="Failed",
                        s_nationkey_source=11,
                        s_suppkey_source=1,
                    )
                ]
            ),
            threshold_mismatch_count=1,
        ),
    )
    assert actual_data_reconcile.mismatch_count == expected_data_reconcile.mismatch_count
    assert actual_data_reconcile.missing_in_src_count == expected_data_reconcile.missing_in_src_count
    assert actual_data_reconcile.missing_in_tgt_count == expected_data_reconcile.missing_in_tgt_count
    assert actual_data_reconcile.mismatch.mismatch_columns == expected_data_reconcile.mismatch.mismatch_columns
    assert actual_data_reconcile.mismatch.mismatch_df is not None
    assert expected_data_reconcile.mismatch.mismatch_df is not None
    assert actual_data_reconcile.missing_in_src is not None
    assert expected_data_reconcile.missing_in_src is not None
    assert actual_data_reconcile.missing_in_tgt is not None
    assert expected_data_reconcile.missing_in_tgt is not None

    assertDataFrameEqual(actual_data_reconcile.mismatch.mismatch_df, expected_data_reconcile.mismatch.mismatch_df)
    assertDataFrameEqual(actual_data_reconcile.missing_in_src, expected_data_reconcile.missing_in_src)
    assertDataFrameEqual(actual_data_reconcile.missing_in_tgt, expected_data_reconcile.missing_in_tgt)
    actual_schema_reconcile = Reconciliation(
        source,
        target,
        database_config,
        "data",
        schema_comparator,
        get_dialect("databricks"),
        spark,
        recon_metadata,
        FakeReconIntermediatePersist(),
    ).reconcile_schema(src_schema, tgt_schema, normalized_table_conf_with_opts)
    expected_schema_reconcile = spark.createDataFrame(
        [
            Row(
                source_column="s_suppkey",
                source_datatype="number",
                databricks_column="s_suppkey_t",
                databricks_datatype="number",
                is_valid=True,
            ),
            Row(
                source_column="s_name",
                source_datatype="varchar",
                databricks_column="s_name",
                databricks_datatype="varchar",
                is_valid=True,
            ),
            Row(
                source_column="s_address",
                source_datatype="varchar",
                databricks_column="s_address_t",
                databricks_datatype="varchar",
                is_valid=True,
            ),
            Row(
                source_column="s_nationkey",
                source_datatype="number",
                databricks_column="s_nationkey_t",
                databricks_datatype="number",
                is_valid=True,
            ),
            Row(
                source_column="s_phone",
                source_datatype="varchar",
                databricks_column="s_phone_t",
                databricks_datatype="varchar",
                is_valid=True,
            ),
            Row(
                source_column="s_acctbal",
                source_datatype="number",
                databricks_column="s_acctbal_t",
                databricks_datatype="number",
                is_valid=True,
            ),
        ]
    )
    assertDataFrameEqual(actual_schema_reconcile.compare_df, expected_schema_reconcile)
    assert actual_schema_reconcile.is_valid is True
    assert actual_data_reconcile.threshold_output.threshold_df is not None
    assertDataFrameEqual(
        actual_data_reconcile.threshold_output.threshold_df,
        spark.createDataFrame(
            [
                Row(
                    s_acctbal_source=100,
                    s_acctbal_databricks=210,
                    s_acctbal_match="Failed",
                    s_nationkey_source=11,
                    s_suppkey_source=1,
                )
            ]
        ),
    )
    assert actual_data_reconcile.threshold_output.threshold_mismatch_count == 1


def test_reconcile_data_without_mismatches_and_missing(
    spark,
    normalized_table_conf_with_opts,
    table_schema_ansi_ansi,
    query_store,
    tmp_path: Path,
    recon_metadata: ReconcileMetadataConfig,
):
    src_schema, tgt_schema = table_schema_ansi_ansi
    source_dataframe_repository = {
        (
            CATALOG,
            SCHEMA,
            query_store.hash_queries.source_hash_query,
        ): spark.createDataFrame(
            [
                Row(hash_value_recon="a1b", s_nationkey=11, s_suppkey=1),
                Row(hash_value_recon="c2d", s_nationkey=22, s_suppkey=2),
            ]
        ),
        (CATALOG, SCHEMA, query_store.threshold_queries.source_threshold_query): spark.createDataFrame(
            [Row(s_nationkey=11, s_suppkey=1, s_acctbal=100)]
        ),
    }
    source_schema_repository = {(CATALOG, SCHEMA, SRC_TABLE): src_schema}
    target_dataframe_repository = {
        (
            CATALOG,
            SCHEMA,
            query_store.hash_queries.target_hash_query,
        ): spark.createDataFrame(
            [
                Row(hash_value_recon="a1b", s_nationkey=11, s_suppkey=1),
                Row(hash_value_recon="c2d", s_nationkey=22, s_suppkey=2),
            ]
        ),
        (CATALOG, SCHEMA, query_store.threshold_queries.target_threshold_query): spark.createDataFrame(
            [Row(s_nationkey=11, s_suppkey=1, s_acctbal=110)]
        ),
        (CATALOG, SCHEMA, query_store.threshold_queries.threshold_comparison_query): spark.createDataFrame(
            [
                Row(
                    s_acctbal_source=100,
                    s_acctbal_databricks=110,
                    s_acctbal_match="Warning",
                    s_nationkey_source=11,
                    s_suppkey_source=1,
                )
            ]
        ),
    }
    target_schema_repository = {(CATALOG, SCHEMA, TGT_TABLE): tgt_schema}
    database_config = DatabaseConfig(
        source_catalog=CATALOG,
        source_schema=SCHEMA,
        target_catalog=CATALOG,
        target_schema=SCHEMA,
    )
    schema_comparator = SchemaCompare(spark)
    source = MockDataSource(source_dataframe_repository, source_schema_repository)
    target = MockDataSource(target_dataframe_repository, target_schema_repository)
    actual = Reconciliation(
        source,
        target,
        database_config,
        "data",
        schema_comparator,
        get_dialect("databricks"),
        spark,
        recon_metadata,
        FakeReconIntermediatePersist(),
    ).reconcile_data(normalized_table_conf_with_opts, src_schema, tgt_schema)
    assert actual.mismatch_count == 0
    assert actual.missing_in_src_count == 0
    assert actual.missing_in_tgt_count == 0
    assert actual.mismatch is None
    assert actual.missing_in_src is None
    assert actual.missing_in_tgt is None
    assert actual.threshold_output.threshold_df is None
    assert actual.threshold_output.threshold_mismatch_count == 0


def test_reconcile_data_with_mismatch_and_no_missing(
    spark,
    normalized_table_conf_with_opts,
    table_schema_ansi_ansi,
    query_store,
    tmp_path: Path,
    recon_metadata: ReconcileMetadataConfig,
):
    src_schema, tgt_schema = table_schema_ansi_ansi
    normalized_table_conf_with_opts.drop_columns = ["`s_acctbal`"]
    normalized_table_conf_with_opts.column_thresholds = None
    source_dataframe_repository = {
        (
            CATALOG,
            SCHEMA,
            query_store.hash_queries.source_hash_query,
        ): spark.createDataFrame(
            [
                Row(hash_value_recon="a1b", s_nationkey=11, s_suppkey=1),
                Row(hash_value_recon="c2d", s_nationkey=22, s_suppkey=2),
            ]
        ),
        (CATALOG, SCHEMA, query_store.mismatch_queries.source_mismatch_query): spark.createDataFrame(
            [Row(s_address="address-2", s_name="name-2", s_nationkey=22, s_phone="222-2", s_suppkey=2)]
        ),
    }
    source_schema_repository = {(CATALOG, SCHEMA, SRC_TABLE): src_schema}
    target_dataframe_repository = {
        (
            CATALOG,
            SCHEMA,
            query_store.hash_queries.target_hash_query,
        ): spark.createDataFrame(
            [
                Row(hash_value_recon="a1b", s_nationkey=11, s_suppkey=1),
                Row(hash_value_recon="c2de", s_nationkey=22, s_suppkey=2),
            ]
        ),
        (
            CATALOG,
            SCHEMA,
            query_store.sampling_queries.target_sampling_query,
        ): spark.createDataFrame(
            [
                Row(hash_value_recon="a1b", s_nationkey=11, s_suppkey=1),
                Row(hash_value_recon="c2de", s_nationkey=22, s_suppkey=2),
            ]
        ),
        (CATALOG, SCHEMA, query_store.mismatch_queries.target_mismatch_query): spark.createDataFrame(
            [Row(s_address="address-22", s_name="name-2", s_nationkey=22, s_phone="222", s_suppkey=2)]
        ),
    }
    target_schema_repository = {(CATALOG, SCHEMA, TGT_TABLE): tgt_schema}
    database_config = DatabaseConfig(
        source_catalog=CATALOG,
        source_schema=SCHEMA,
        target_catalog=CATALOG,
        target_schema=SCHEMA,
    )
    schema_comparator = SchemaCompare(spark)
    source = MockDataSource(source_dataframe_repository, source_schema_repository)
    target = MockDataSource(target_dataframe_repository, target_schema_repository)
    actual = Reconciliation(
        source,
        target,
        database_config,
        "data",
        schema_comparator,
        get_dialect("databricks"),
        spark,
        recon_metadata,
        FakeReconIntermediatePersist(),
    ).reconcile_data(normalized_table_conf_with_opts, src_schema, tgt_schema)
    expected = DataReconcileOutput(
        mismatch_count=1,
        missing_in_src_count=0,
        missing_in_tgt_count=0,
        missing_in_src=None,
        missing_in_tgt=None,
        mismatch=MismatchOutput(
            mismatch_df=spark.createDataFrame(
                [
                    Row(
                        s_suppkey=2,
                        s_nationkey=22,
                        s_address_base="address-2",
                        s_address_compare="address-22",
                        s_address_match=False,
                        s_name_base="name-2",
                        s_name_compare="name-2",
                        s_name_match=True,
                        s_phone_base="222-2",
                        s_phone_compare="222",
                        s_phone_match=False,
                    )
                ]
            ),
            mismatch_columns=["s_address", "s_phone"],
        ),
    )
    assert actual.mismatch_count == expected.mismatch_count
    assert actual.missing_in_src_count == expected.missing_in_src_count
    assert actual.missing_in_tgt_count == expected.missing_in_tgt_count
    assert actual.mismatch.mismatch_columns == expected.mismatch.mismatch_columns
    assert actual.missing_in_src is None
    assert actual.missing_in_tgt is None
    assert actual.mismatch.mismatch_df is not None
    assert expected.mismatch.mismatch_df is not None
    assertDataFrameEqual(actual.mismatch.mismatch_df, expected.mismatch.mismatch_df)


def test_reconcile_data_missing_and_no_mismatch(
    spark,
    normalized_table_conf_with_opts,
    table_schema_ansi_ansi,
    query_store,
    tmp_path: Path,
    recon_metadata: ReconcileMetadataConfig,
):
    src_schema, tgt_schema = table_schema_ansi_ansi
    normalized_table_conf_with_opts.drop_columns = ["`s_acctbal`"]
    normalized_table_conf_with_opts.column_thresholds = None
    source_dataframe_repository = {
        (
            CATALOG,
            SCHEMA,
            query_store.hash_queries.source_hash_query,
        ): spark.createDataFrame(
            [
                Row(hash_value_recon="a1b", s_nationkey=11, s_suppkey=1),
                Row(hash_value_recon="c2d", s_nationkey=22, s_suppkey=2),
                Row(hash_value_recon="e3g", s_nationkey=33, s_suppkey=3),
            ]
        ),
        (CATALOG, SCHEMA, query_store.missing_queries.target_missing_query): spark.createDataFrame(
            [Row(s_address="address-3", s_name="name-3", s_nationkey=33, s_phone="333", s_suppkey=3)]
        ),
    }
    source_schema_repository = {(CATALOG, SCHEMA, SRC_TABLE): src_schema}
    target_dataframe_repository = {
        (
            CATALOG,
            SCHEMA,
            query_store.hash_queries.target_hash_query,
        ): spark.createDataFrame(
            [
                Row(hash_value_recon="a1b", s_nationkey=11, s_suppkey=1),
                Row(hash_value_recon="c2d", s_nationkey=22, s_suppkey=2),
                Row(hash_value_recon="k4l", s_nationkey=44, s_suppkey=4),
            ]
        ),
        (CATALOG, SCHEMA, query_store.missing_queries.source_missing_query): spark.createDataFrame(
            [Row(s_address="address-4", s_name="name-4", s_nationkey=44, s_phone="444", s_suppkey=4)]
        ),
    }
    target_schema_repository = {(CATALOG, SCHEMA, TGT_TABLE): tgt_schema}
    database_config = DatabaseConfig(
        source_catalog=CATALOG,
        source_schema=SCHEMA,
        target_catalog=CATALOG,
        target_schema=SCHEMA,
    )
    schema_comparator = SchemaCompare(spark)
    source = MockDataSource(source_dataframe_repository, source_schema_repository)
    target = MockDataSource(target_dataframe_repository, target_schema_repository)
    actual = Reconciliation(
        source,
        target,
        database_config,
        "data",
        schema_comparator,
        get_dialect("databricks"),
        spark,
        recon_metadata,
        FakeReconIntermediatePersist(),
    ).reconcile_data(normalized_table_conf_with_opts, src_schema, tgt_schema)
    expected = DataReconcileOutput(
        mismatch_count=0,
        missing_in_src_count=1,
        missing_in_tgt_count=1,
        missing_in_src=spark.createDataFrame(
            [Row(s_address="address-4", s_name="name-4", s_nationkey=44, s_phone="444", s_suppkey=4)]
        ),
        missing_in_tgt=spark.createDataFrame(
            [Row(s_address="address-3", s_name="name-3", s_nationkey=33, s_phone="333", s_suppkey=3)]
        ),
        mismatch=MismatchOutput(),
    )
    assert actual.mismatch_count == expected.mismatch_count
    assert actual.missing_in_src_count == expected.missing_in_src_count
    assert actual.missing_in_tgt_count == expected.missing_in_tgt_count
    assert actual.mismatch is None
    assertDataFrameEqual(actual.missing_in_src, expected.missing_in_src)
    assertDataFrameEqual(actual.missing_in_tgt, expected.missing_in_tgt)


@pytest.fixture
def mock_for_report_type_data(
    normalized_table_conf_with_opts,
    table_schema_ansi_ansi,
    query_store,
    recon_metadata,
    spark,
):
    normalized_table_conf_with_opts.drop_columns = ["`s_acctbal`"]
    normalized_table_conf_with_opts.column_thresholds = None
    table_recon = TableRecon(
        tables=[normalized_table_conf_with_opts],
    )
    src_schema, tgt_schema = table_schema_ansi_ansi
    source_dataframe_repository = {
        (
            CATALOG,
            SCHEMA,
            query_store.hash_queries.source_hash_query,
        ): spark.createDataFrame(
            [
                Row(hash_value_recon="a1b", s_nationkey=11, s_suppkey=1),
                Row(hash_value_recon="c2d", s_nationkey=22, s_suppkey=2),
                Row(hash_value_recon="e3g", s_nationkey=33, s_suppkey=3),
            ]
        ),
        (CATALOG, SCHEMA, query_store.mismatch_queries.source_mismatch_query): spark.createDataFrame(
            [Row(s_address="address-2", s_name="name-2", s_nationkey=22, s_phone="222-2", s_suppkey=2)]
        ),
        (CATALOG, SCHEMA, query_store.missing_queries.target_missing_query): spark.createDataFrame(
            [Row(s_address="address-3", s_name="name-3", s_nationkey=33, s_phone="333", s_suppkey=3)]
        ),
        (CATALOG, SCHEMA, query_store.record_count_queries.source_record_count_query): spark.createDataFrame(
            [Row(count=3)]
        ),
    }
    source_schema_repository = {(CATALOG, SCHEMA, SRC_TABLE): src_schema}
    target_dataframe_repository = {
        (
            CATALOG,
            SCHEMA,
            query_store.hash_queries.target_hash_query,
        ): spark.createDataFrame(
            [
                Row(hash_value_recon="a1b", s_nationkey=11, s_suppkey=1),
                Row(hash_value_recon="c2de", s_nationkey=22, s_suppkey=2),
                Row(hash_value_recon="k4l", s_nationkey=44, s_suppkey=4),
            ]
        ),
        (
            CATALOG,
            SCHEMA,
            query_store.sampling_queries.target_sampling_query,
        ): spark.createDataFrame(
            [
                Row(hash_value_recon="a1b", s_nationkey=11, s_suppkey=1),
                Row(hash_value_recon="c2de", s_nationkey=22, s_suppkey=2),
                Row(hash_value_recon="k4l", s_nationkey=44, s_suppkey=4),
            ]
        ),
        (CATALOG, SCHEMA, query_store.mismatch_queries.target_mismatch_query): spark.createDataFrame(
            [Row(s_address="address-22", s_name="name-2", s_nationkey=22, s_phone="222", s_suppkey=2)]
        ),
        (CATALOG, SCHEMA, query_store.missing_queries.source_missing_query): spark.createDataFrame(
            [Row(s_address="address-4", s_name="name-4", s_nationkey=44, s_phone="444", s_suppkey=4)]
        ),
        (CATALOG, SCHEMA, query_store.record_count_queries.target_record_count_query): spark.createDataFrame(
            [Row(count=3)]
        ),
    }
    target_schema_repository = {(CATALOG, SCHEMA, TGT_TABLE): tgt_schema}
    source = MockDataSource(source_dataframe_repository, source_schema_repository)
    target = MockDataSource(target_dataframe_repository, target_schema_repository)
    reconcile_config_data = ReconcileConfig(
        report_type="data",
        source=SourceConnectionConfig(
            dialect="databricks",
            catalog=CATALOG,
            schema=SCHEMA,
        ),
        target=TargetConnectionConfig(
            catalog=CATALOG,
            schema=SCHEMA,
        ),
        metadata_config=recon_metadata,
    )
    return table_recon, source, target, reconcile_config_data


def test_recon_for_report_type_is_data(
    mock_workspace_client, spark, report_tables_schema, mock_for_report_type_data, tmp_path: Path, recon_id: UUID
):
    recon_schema, metrics_schema, details_schema = report_tables_schema
    table_recon, source, target, reconcile_config_data = mock_for_report_type_data
    catalog = reconcile_config_data.metadata_config.catalog
    schema = reconcile_config_data.metadata_config.schema
    with (
        patch("databricks.labs.lakebridge.reconcile.trigger_recon_service.datetime") as mock_datetime,
        patch("databricks.labs.lakebridge.reconcile.recon_capture.datetime") as recon_datetime,
        patch("databricks.labs.lakebridge.reconcile.utils.initialise_data_source", return_value=(source, target)),
        patch(
            "databricks.labs.lakebridge.reconcile.trigger_recon_service.uuid4",
            return_value=recon_id,
        ),
        patch(
            "databricks.labs.lakebridge.reconcile.recon_capture.ReconCapture._generate_recon_main_id",
            return_value=11111111111,
        ),
    ):
        mock_datetime.now.return_value = MOCK_TIMESTAMP
        recon_datetime.now.return_value = MOCK_TIMESTAMP

        reconcile_output = TriggerReconService.trigger_recon(
            mock_workspace_client, spark, table_recon, reconcile_config_data
        )

        assert reconcile_output.recon_id == recon_id.hex

    expected_remorph_recon = spark.createDataFrame(
        data=[
            (
                11111111111,
                recon_id.hex,
                "Databricks",
                ("org", "data", "supplier"),
                ("org", "data", "target_supplier"),
                "data",
                "reconcile",
                MOCK_TIMESTAMP,
                MOCK_TIMESTAMP,
            )
        ],
        schema=recon_schema,
    )
    expected_remorph_recon_metrics = spark.createDataFrame(
        data=[
            (
                11111111111,
                (3, 3, (1, 1), (1, 0, "s_address,s_phone"), None),
                (False, "remorph", ""),
                MOCK_TIMESTAMP,
            )
        ],
        schema=metrics_schema,
    )
    expected_remorph_recon_details = spark.createDataFrame(
        data=[
            (
                11111111111,
                "mismatch",
                False,
                [
                    {
                        "s_suppkey": "2",
                        "s_nationkey": "22",
                        "s_address_base": "address-2",
                        "s_address_compare": "address-22",
                        "s_address_match": "false",
                        "s_name_base": "name-2",
                        "s_name_compare": "name-2",
                        "s_name_match": "true",
                        "s_phone_base": "222-2",
                        "s_phone_compare": "222",
                        "s_phone_match": "false",
                    }
                ],
                MOCK_TIMESTAMP,
            ),
            (
                11111111111,
                "missing_in_source",
                False,
                [
                    {
                        "s_address": "address-4",
                        "s_name": "name-4",
                        "s_nationkey": "44",
                        "s_phone": "444",
                        "s_suppkey": "4",
                    }
                ],
                MOCK_TIMESTAMP,
            ),
            (
                11111111111,
                "missing_in_target",
                False,
                [
                    {
                        "s_address": "address-3",
                        "s_name": "name-3",
                        "s_nationkey": "33",
                        "s_phone": "333",
                        "s_suppkey": "3",
                    }
                ],
                MOCK_TIMESTAMP,
            ),
        ],
        schema=details_schema,
    )

    assertDataFrameEqual(
        spark.sql(f"SELECT * FROM {catalog}.{schema}.MAIN"), expected_remorph_recon, ignoreNullable=True
    )
    assertDataFrameEqual(
        spark.sql(f"SELECT * FROM {catalog}.{schema}.METRICS"), expected_remorph_recon_metrics, ignoreNullable=True
    )
    assertDataFrameEqual(
        spark.sql(f"SELECT * FROM {catalog}.{schema}.DETAILS"), expected_remorph_recon_details, ignoreNullable=True
    )


@pytest.fixture
def mock_for_report_type_schema(
    normalized_table_conf_with_opts,
    table_schema_ansi_ansi,
    query_store,
    spark,
    recon_metadata: ReconcileMetadataConfig,
):
    table_recon = TableRecon(
        tables=[normalized_table_conf_with_opts],
    )
    src_schema, tgt_schema = table_schema_ansi_ansi
    source_dataframe_repository = {
        (
            CATALOG,
            SCHEMA,
            query_store.hash_queries.source_hash_query,
        ): spark.createDataFrame(
            [
                Row(hash_value_recon="a1b", s_nationkey=11, s_suppkey=1),
                Row(hash_value_recon="c2d", s_nationkey=22, s_suppkey=2),
                Row(hash_value_recon="e3g", s_nationkey=33, s_suppkey=3),
            ]
        ),
        (CATALOG, SCHEMA, query_store.mismatch_queries.source_mismatch_query): spark.createDataFrame(
            [Row(s_address="address-2", s_name="name-2", s_nationkey=22, s_phone="222-2", s_suppkey=2)]
        ),
        (CATALOG, SCHEMA, query_store.missing_queries.target_missing_query): spark.createDataFrame(
            [Row(s_address="address-3", s_name="name-3", s_nationkey=33, s_phone="333", s_suppkey=3)]
        ),
        (CATALOG, SCHEMA, query_store.record_count_queries.source_record_count_query): spark.createDataFrame(
            [Row(count=3)]
        ),
    }
    source_schema_repository = {(CATALOG, SCHEMA, SRC_TABLE): src_schema}

    target_dataframe_repository = {
        (
            CATALOG,
            SCHEMA,
            query_store.hash_queries.target_hash_query,
        ): spark.createDataFrame(
            [
                Row(hash_value_recon="a1b", s_nationkey=11, s_suppkey=1),
                Row(hash_value_recon="c2de", s_nationkey=22, s_suppkey=2),
                Row(hash_value_recon="k4l", s_nationkey=44, s_suppkey=4),
            ]
        ),
        (CATALOG, SCHEMA, query_store.mismatch_queries.target_mismatch_query): spark.createDataFrame(
            [Row(s_address="address-22", s_name="name-2", s_nationkey=22, s_phone="222", s_suppkey=2)]
        ),
        (CATALOG, SCHEMA, query_store.missing_queries.source_missing_query): spark.createDataFrame(
            [Row(s_address="address-4", s_name="name-4", s_nationkey=44, s_phone="444", s_suppkey=4)]
        ),
        (CATALOG, SCHEMA, query_store.record_count_queries.target_record_count_query): spark.createDataFrame(
            [Row(count=3)]
        ),
    }
    target_schema_repository = {(CATALOG, SCHEMA, TGT_TABLE): tgt_schema}
    source = MockDataSource(source_dataframe_repository, source_schema_repository)
    target = MockDataSource(target_dataframe_repository, target_schema_repository)
    reconcile_config_schema = ReconcileConfig(
        report_type="schema",
        source=SourceConnectionConfig(
            dialect="databricks",
            catalog=CATALOG,
            schema=SCHEMA,
        ),
        target=TargetConnectionConfig(
            catalog=CATALOG,
            schema=SCHEMA,
        ),
        metadata_config=recon_metadata,
    )
    return table_recon, source, target, reconcile_config_schema


def test_recon_for_report_type_schema(
    mock_workspace_client, spark, report_tables_schema, mock_for_report_type_schema, tmp_path: Path, recon_id: UUID
):
    recon_schema, metrics_schema, details_schema = report_tables_schema
    table_recon, source, target, reconcile_config_schema = mock_for_report_type_schema
    catalog = reconcile_config_schema.metadata_config.catalog
    schema = reconcile_config_schema.metadata_config.schema
    with (
        patch("databricks.labs.lakebridge.reconcile.trigger_recon_service.datetime") as mock_datetime,
        patch("databricks.labs.lakebridge.reconcile.recon_capture.datetime") as recon_datetime,
        patch("databricks.labs.lakebridge.reconcile.utils.initialise_data_source", return_value=(source, target)),
        patch("databricks.labs.lakebridge.reconcile.trigger_recon_service.uuid4", return_value=recon_id),
        patch(
            "databricks.labs.lakebridge.reconcile.recon_capture.ReconCapture._generate_recon_main_id",
            return_value=22222222222,
        ),
    ):
        mock_datetime.now.return_value = MOCK_TIMESTAMP
        recon_datetime.now.return_value = MOCK_TIMESTAMP
        final_reconcile_output = TriggerReconService.trigger_recon(
            mock_workspace_client, spark, table_recon, reconcile_config_schema
        )

    expected_remorph_recon = spark.createDataFrame(
        data=[
            (
                22222222222,
                recon_id.hex,
                "Databricks",
                ("org", "data", "supplier"),
                ("org", "data", "target_supplier"),
                "schema",
                "reconcile",
                MOCK_TIMESTAMP,
                MOCK_TIMESTAMP,
            )
        ],
        schema=recon_schema,
    )
    expected_remorph_recon_metrics = spark.createDataFrame(
        data=[
            (
                22222222222,
                (0, 0, None, None, True),
                (True, "remorph", ""),
                MOCK_TIMESTAMP,
            )
        ],
        schema=metrics_schema,
    )
    expected_remorph_recon_details = spark.createDataFrame(
        data=[
            (
                22222222222,
                "schema",
                True,
                [
                    {
                        "source_column": "s_suppkey",
                        "source_datatype": "number",
                        "databricks_column": "s_suppkey_t",
                        "databricks_datatype": "number",
                        "is_valid": "true",
                    },
                    {
                        "source_column": "s_name",
                        "source_datatype": "varchar",
                        "databricks_column": "s_name",
                        "databricks_datatype": "varchar",
                        "is_valid": "true",
                    },
                    {
                        "source_column": "s_address",
                        "source_datatype": "varchar",
                        "databricks_column": "s_address_t",
                        "databricks_datatype": "varchar",
                        "is_valid": "true",
                    },
                    {
                        "source_column": "s_nationkey",
                        "source_datatype": "number",
                        "databricks_column": "s_nationkey_t",
                        "databricks_datatype": "number",
                        "is_valid": "true",
                    },
                    {
                        "source_column": "s_phone",
                        "source_datatype": "varchar",
                        "databricks_column": "s_phone_t",
                        "databricks_datatype": "varchar",
                        "is_valid": "true",
                    },
                    {
                        "source_column": "s_acctbal",
                        "source_datatype": "number",
                        "databricks_column": "s_acctbal_t",
                        "databricks_datatype": "number",
                        "is_valid": "true",
                    },
                ],
                MOCK_TIMESTAMP,
            )
        ],
        schema=details_schema,
    )

    assertDataFrameEqual(
        spark.sql(f"SELECT * FROM {catalog}.{schema}.MAIN"), expected_remorph_recon, ignoreNullable=True
    )
    assertDataFrameEqual(
        spark.sql(f"SELECT * FROM {catalog}.{schema}.METRICS"), expected_remorph_recon_metrics, ignoreNullable=True
    )
    assertDataFrameEqual(
        spark.sql(f"SELECT * FROM {catalog}.{schema}.DETAILS"), expected_remorph_recon_details, ignoreNullable=True
    )

    assert final_reconcile_output.recon_id == recon_id.hex


@pytest.fixture
def mock_for_report_type_all(
    mock_workspace_client,
    normalized_table_conf_with_opts,
    table_schema_oracle_ansi,
    spark,
    query_store,
    recon_metadata,
):
    snowflake_query_store = query_store  # TODO: Implement snowflake query store
    normalized_table_conf_with_opts.drop_columns = ["`s_acctbal`"]
    normalized_table_conf_with_opts.column_thresholds = None
    table_recon = TableRecon(
        tables=[normalized_table_conf_with_opts],
    )
    src_schema, tgt_schema = table_schema_oracle_ansi
    source_dataframe_repository = {
        (
            CATALOG,
            SCHEMA,
            snowflake_query_store.hash_queries.source_hash_query,
        ): spark.createDataFrame(
            [
                Row(hash_value_recon="a1b", s_nationkey=11, s_suppkey=1),
                Row(hash_value_recon="c2d", s_nationkey=22, s_suppkey=2),
                Row(hash_value_recon="e3g", s_nationkey=33, s_suppkey=3),
            ]
        ),
        (CATALOG, SCHEMA, snowflake_query_store.mismatch_queries.source_mismatch_query): spark.createDataFrame(
            [Row(s_address="address-2", s_name="name-2", s_nationkey=22, s_phone="222-2", s_suppkey=2)]
        ),
        (CATALOG, SCHEMA, snowflake_query_store.missing_queries.target_missing_query): spark.createDataFrame(
            [Row(s_address="address-3", s_name="name-3", s_nationkey=33, s_phone="333", s_suppkey=3)]
        ),
        (
            CATALOG,
            SCHEMA,
            snowflake_query_store.record_count_queries.source_record_count_query,
        ): spark.createDataFrame([Row(count=3)]),
    }
    source_schema_repository = {(CATALOG, SCHEMA, SRC_TABLE): src_schema}

    target_dataframe_repository = {
        (
            CATALOG,
            SCHEMA,
            snowflake_query_store.hash_queries.target_hash_query,
        ): spark.createDataFrame(
            [
                Row(hash_value_recon="a1b", s_nationkey=11, s_suppkey=1),
                Row(hash_value_recon="c2de", s_nationkey=22, s_suppkey=2),
                Row(hash_value_recon="k4l", s_nationkey=44, s_suppkey=4),
            ]
        ),
        (
            CATALOG,
            SCHEMA,
            snowflake_query_store.sampling_queries.target_sampling_query,
        ): spark.createDataFrame(
            [
                Row(hash_value_recon="a1b", s_nationkey=11, s_suppkey=1),
                Row(hash_value_recon="c2de", s_nationkey=22, s_suppkey=2),
                Row(hash_value_recon="k4l", s_nationkey=44, s_suppkey=4),
            ]
        ),
        (CATALOG, SCHEMA, snowflake_query_store.mismatch_queries.target_mismatch_query): spark.createDataFrame(
            [Row(s_address="address-22", s_name="name-2", s_nationkey=22, s_phone="222", s_suppkey=2)]
        ),
        (CATALOG, SCHEMA, snowflake_query_store.missing_queries.source_missing_query): spark.createDataFrame(
            [Row(s_address="address-4", s_name="name-4", s_nationkey=44, s_phone="444", s_suppkey=4)]
        ),
        (
            CATALOG,
            SCHEMA,
            snowflake_query_store.record_count_queries.target_record_count_query,
        ): spark.createDataFrame([Row(count=3)]),
    }

    target_schema_repository = {(CATALOG, SCHEMA, TGT_TABLE): tgt_schema}
    source = MockDataSource(source_dataframe_repository, source_schema_repository)
    target = MockDataSource(target_dataframe_repository, target_schema_repository)
    reconcile_config_all = ReconcileConfig(
        report_type="all",
        source=SourceConnectionConfig(
            dialect="snowflake",
            catalog=CATALOG,
            schema=SCHEMA,
            uc_connection_name="remorph_snowflake",
        ),
        target=TargetConnectionConfig(
            catalog=CATALOG,
            schema=SCHEMA,
        ),
        metadata_config=recon_metadata,
    )
    return table_recon, source, target, reconcile_config_all


@pytest.mark.skip(reason="Will be fixed in a following PR")
def test_recon_for_report_type_all(
    mock_workspace_client,
    spark,
    report_tables_schema,
    mock_for_report_type_all,
    tmp_path: Path,
):
    recon_schema, metrics_schema, details_schema = report_tables_schema
    table_recon, source, target, reconcile_config_all = mock_for_report_type_all
    catalog = reconcile_config_all.metadata_config.catalog
    schema = reconcile_config_all.metadata_config.schema

    with (
        patch("databricks.labs.lakebridge.reconcile.trigger_recon_service.datetime") as mock_datetime,
        patch("databricks.labs.lakebridge.reconcile.recon_capture.datetime") as recon_datetime,
        patch("databricks.labs.lakebridge.reconcile.utils.initialise_data_source", return_value=(source, target)),
        patch(
            "databricks.labs.lakebridge.reconcile.trigger_recon_service.uuid4",
            return_value="00112233-4455-6677-8899-aabbccddeeff",
        ),
        patch(
            "databricks.labs.lakebridge.reconcile.recon_capture.ReconCapture._generate_recon_main_id",
            return_value=33333333333,
        ),
        patch("databricks.labs.lakebridge.reconcile.utils.generate_volume_path", return_value=str(tmp_path)),
    ):
        mock_datetime.now.return_value = MOCK_TIMESTAMP
        recon_datetime.now.return_value = MOCK_TIMESTAMP
        with pytest.raises(ReconciliationException) as exc_info:
            TriggerReconService.trigger_recon(mock_workspace_client, spark, table_recon, reconcile_config_all)
        if exc_info.value.reconcile_output is not None:
            assert exc_info.value.reconcile_output.recon_id == "00112233-4455-6677-8899-aabbccddeeff"

    expected_remorph_recon = spark.createDataFrame(
        data=[
            (
                33333333333,
                "00112233-4455-6677-8899-aabbccddeeff",
                "Snowflake",
                ("org", "data", "supplier"),
                ("org", "data", "target_supplier"),
                "all",
                "reconcile",
                MOCK_TIMESTAMP,
                MOCK_TIMESTAMP,
            )
        ],
        schema=recon_schema,
    )
    expected_remorph_recon_metrics = spark.createDataFrame(
        data=[
            (
                33333333333,
                (3, 3, (1, 1), (1, 0, "s_address,s_phone"), False),
                (False, "remorph", ""),
                MOCK_TIMESTAMP,
            )
        ],
        schema=metrics_schema,
    )
    expected_remorph_recon_details = spark.createDataFrame(
        data=[
            (
                33333333333,
                "mismatch",
                False,
                [
                    {
                        "s_suppkey": "2",
                        "s_nationkey": "22",
                        "s_address_base": "address-2",
                        "s_address_compare": "address-22",
                        "s_address_match": "false",
                        "s_name_base": "name-2",
                        "s_name_compare": "name-2",
                        "s_name_match": "true",
                        "s_phone_base": "222-2",
                        "s_phone_compare": "222",
                        "s_phone_match": "false",
                    }
                ],
                MOCK_TIMESTAMP,
            ),
            (
                33333333333,
                "missing_in_source",
                False,
                [
                    {
                        "s_address": "address-4",
                        "s_name": "name-4",
                        "s_nationkey": "44",
                        "s_phone": "444",
                        "s_suppkey": "4",
                    }
                ],
                MOCK_TIMESTAMP,
            ),
            (
                33333333333,
                "missing_in_target",
                False,
                [
                    {
                        "s_address": "address-3",
                        "s_name": "name-3",
                        "s_nationkey": "33",
                        "s_phone": "333",
                        "s_suppkey": "3",
                    }
                ],
                MOCK_TIMESTAMP,
            ),
            (
                33333333333,
                "schema",
                False,
                [
                    {
                        "source_column": "s_suppkey",
                        "source_datatype": "number",
                        "databricks_column": "s_suppkey_t",
                        "databricks_datatype": "number",
                        "is_valid": "false",
                    },
                    {
                        "source_column": "s_name",
                        "source_datatype": "varchar",
                        "databricks_column": "s_name",
                        "databricks_datatype": "varchar",
                        "is_valid": "false",
                    },
                    {
                        "source_column": "s_address",
                        "source_datatype": "varchar",
                        "databricks_column": "s_address_t",
                        "databricks_datatype": "varchar",
                        "is_valid": "false",
                    },
                    {
                        "source_column": "s_nationkey",
                        "source_datatype": "number",
                        "databricks_column": "s_nationkey_t",
                        "databricks_datatype": "number",
                        "is_valid": "false",
                    },
                    {
                        "source_column": "s_phone",
                        "source_datatype": "varchar",
                        "databricks_column": "s_phone_t",
                        "databricks_datatype": "varchar",
                        "is_valid": "false",
                    },
                ],
                MOCK_TIMESTAMP,
            ),
        ],
        schema=details_schema,
    )

    assertDataFrameEqual(
        spark.sql(f"SELECT * FROM {catalog}.{schema}.MAIN"), expected_remorph_recon, ignoreNullable=True
    )
    assertDataFrameEqual(
        spark.sql(f"SELECT * FROM {catalog}.{schema}.METRICS"), expected_remorph_recon_metrics, ignoreNullable=True
    )
    assertDataFrameEqual(
        spark.sql(f"SELECT * FROM {catalog}.{schema}.DETAILS"), expected_remorph_recon_details, ignoreNullable=True
    )


@pytest.fixture
def mock_for_report_type_row(
    normalized_table_conf_with_opts, table_schema_ansi_ansi, spark, query_store, recon_metadata
):
    normalized_table_conf_with_opts.drop_columns = ["`s_acctbal`"]
    normalized_table_conf_with_opts.column_thresholds = None
    table_recon = TableRecon(
        tables=[normalized_table_conf_with_opts],
    )
    src_schema, tgt_schema = table_schema_ansi_ansi
    source_dataframe_repository = {
        (
            CATALOG,
            SCHEMA,
            query_store.row_queries.source_row_query,
        ): spark.createDataFrame(
            [
                Row(
                    hash_value_recon="a1b",
                    s_address="address-1",
                    s_name="name-1",
                    s_nationkey=11,
                    s_phone="111",
                    s_suppkey=1,
                ),
                Row(
                    hash_value_recon="c2d",
                    s_address="address-2",
                    s_name="name-2",
                    s_nationkey=22,
                    s_phone="222-2",
                    s_suppkey=2,
                ),
                Row(
                    hash_value_recon="e3g",
                    s_address="address-3",
                    s_name="name-3",
                    s_nationkey=33,
                    s_phone="333",
                    s_suppkey=3,
                ),
            ]
        ),
        (CATALOG, SCHEMA, query_store.record_count_queries.source_record_count_query): spark.createDataFrame(
            [Row(count=3)]
        ),
    }
    source_schema_repository = {(CATALOG, SCHEMA, SRC_TABLE): src_schema}

    target_dataframe_repository = {
        (
            CATALOG,
            SCHEMA,
            query_store.row_queries.target_row_query,
        ): spark.createDataFrame(
            [
                Row(
                    hash_value_recon="a1b",
                    s_address="address-1",
                    s_name="name-1",
                    s_nationkey=11,
                    s_phone="111",
                    s_suppkey=1,
                ),
                Row(
                    hash_value_recon="c2de",
                    s_address="address-2",
                    s_name="name-2",
                    s_nationkey=22,
                    s_phone="222",
                    s_suppkey=2,
                ),
                Row(
                    hash_value_recon="h4k",
                    s_address="address-4",
                    s_name="name-4",
                    s_nationkey=44,
                    s_phone="444",
                    s_suppkey=4,
                ),
            ]
        ),
        (CATALOG, SCHEMA, query_store.record_count_queries.target_record_count_query): spark.createDataFrame(
            [Row(count=3)]
        ),
    }

    target_schema_repository = {(CATALOG, SCHEMA, TGT_TABLE): tgt_schema}
    source = MockDataSource(source_dataframe_repository, source_schema_repository)
    target = MockDataSource(target_dataframe_repository, target_schema_repository)
    reconcile_config_row = ReconcileConfig(
        report_type="row",
        source=SourceConnectionConfig(
            dialect="snowflake",
            catalog=CATALOG,
            schema=SCHEMA,
            uc_connection_name="remorph_snowflake",
        ),
        target=TargetConnectionConfig(
            catalog=CATALOG,
            schema=SCHEMA,
        ),
        metadata_config=recon_metadata,
    )

    return source, target, table_recon, reconcile_config_row


@pytest.mark.skip(reason="Will be fixed in a following PR")
def test_recon_for_report_type_is_row(
    mock_workspace_client,
    spark,
    mock_for_report_type_row,
    report_tables_schema,
    tmp_path: Path,
):
    recon_schema, metrics_schema, details_schema = report_tables_schema
    source, target, table_recon, reconcile_config_row = mock_for_report_type_row
    catalog = reconcile_config_row.metadata_config.catalog
    schema = reconcile_config_row.metadata_config.schema
    with (
        patch("databricks.labs.lakebridge.reconcile.trigger_recon_service.datetime") as mock_datetime,
        patch("databricks.labs.lakebridge.reconcile.recon_capture.datetime") as recon_datetime,
        patch("databricks.labs.lakebridge.reconcile.utils.initialise_data_source", return_value=(source, target)),
        patch(
            "databricks.labs.lakebridge.reconcile.trigger_recon_service.uuid4",
            return_value="00112233-4455-6677-8899-aabbccddeeff",
        ),
        patch(
            "databricks.labs.lakebridge.reconcile.recon_capture.ReconCapture._generate_recon_main_id",
            return_value=33333333333,
        ),
        patch("databricks.labs.lakebridge.reconcile.utils.generate_volume_path", return_value=str(tmp_path)),
    ):
        mock_datetime.now.return_value = MOCK_TIMESTAMP
        recon_datetime.now.return_value = MOCK_TIMESTAMP
        with pytest.raises(ReconciliationException) as exc_info:
            TriggerReconService.trigger_recon(mock_workspace_client, spark, table_recon, reconcile_config_row)

        if exc_info.value.reconcile_output is not None:
            assert exc_info.value.reconcile_output.recon_id == "00112233-4455-6677-8899-aabbccddeeff"

    expected_remorph_recon = spark.createDataFrame(
        data=[
            (
                33333333333,
                "00112233-4455-6677-8899-aabbccddeeff",
                "Snowflake",
                ("org", "data", "supplier"),
                ("org", "data", "target_supplier"),
                "row",
                "reconcile",
                MOCK_TIMESTAMP,
                MOCK_TIMESTAMP,
            )
        ],
        schema=recon_schema,
    )
    expected_remorph_recon_metrics = spark.createDataFrame(
        data=[
            (
                33333333333,
                (3, 3, (2, 2), None, None),
                (False, "remorph", ""),
                MOCK_TIMESTAMP,
            )
        ],
        schema=metrics_schema,
    )
    expected_remorph_recon_details = spark.createDataFrame(
        data=[
            (
                33333333333,
                "missing_in_source",
                False,
                [
                    {
                        's_address': 'address-2',
                        's_name': 'name-2',
                        's_nationkey': '22',
                        's_phone': '222',
                        's_suppkey': '2',
                    },
                    {
                        's_address': 'address-4',
                        's_name': 'name-4',
                        's_nationkey': '44',
                        's_phone': '444',
                        's_suppkey': '4',
                    },
                ],
                MOCK_TIMESTAMP,
            ),
            (
                33333333333,
                "missing_in_target",
                False,
                [
                    {
                        's_address': 'address-2',
                        's_name': 'name-2',
                        's_nationkey': '22',
                        's_phone': '222-2',
                        's_suppkey': '2',
                    },
                    {
                        's_address': 'address-3',
                        's_name': 'name-3',
                        's_nationkey': '33',
                        's_phone': '333',
                        's_suppkey': '3',
                    },
                ],
                MOCK_TIMESTAMP,
            ),
        ],
        schema=details_schema,
    )

    assertDataFrameEqual(
        spark.sql(f"SELECT * FROM {catalog}.{schema}.MAIN"), expected_remorph_recon, ignoreNullable=True
    )
    assertDataFrameEqual(
        spark.sql(f"SELECT * FROM {catalog}.{schema}.METRICS"), expected_remorph_recon_metrics, ignoreNullable=True
    )
    assertDataFrameEqual(
        spark.sql(f"SELECT * FROM {catalog}.{schema}.DETAILS"), expected_remorph_recon_details, ignoreNullable=True
    )


@pytest.fixture
def mock_for_recon_exception(normalized_table_conf_with_opts, recon_metadata):
    normalized_table_conf_with_opts.drop_columns = ["s_acctbal"]
    normalized_table_conf_with_opts.column_thresholds = None
    normalized_table_conf_with_opts.join_columns = None
    table_recon = TableRecon(
        tables=[normalized_table_conf_with_opts],
    )
    source = MockDataSource({}, {})
    target = MockDataSource({}, {})
    reconcile_config_exception = ReconcileConfig(
        report_type="all",
        source=SourceConnectionConfig(
            dialect="snowflake",
            catalog=CATALOG,
            schema=SCHEMA,
            uc_connection_name="remorph_snowflake",
        ),
        target=TargetConnectionConfig(
            catalog=CATALOG,
            schema=SCHEMA,
        ),
        metadata_config=recon_metadata,
    )

    return table_recon, source, target, reconcile_config_exception


def test_schema_recon_with_data_source_exception(
    mock_workspace_client, spark, report_tables_schema, mock_for_recon_exception, tmp_path: Path, recon_id: UUID
):
    recon_schema, metrics_schema, details_schema = report_tables_schema
    table_recon, source, target, reconcile_config_exception = mock_for_recon_exception
    catalog = reconcile_config_exception.metadata_config.catalog
    schema = reconcile_config_exception.metadata_config.schema
    reconcile_config_exception.report_type = "schema"
    with (
        patch("databricks.labs.lakebridge.reconcile.trigger_recon_service.datetime") as mock_datetime,
        patch("databricks.labs.lakebridge.reconcile.recon_capture.datetime") as recon_datetime,
        patch("databricks.labs.lakebridge.reconcile.utils.initialise_data_source", return_value=(source, target)),
        patch(
            "databricks.labs.lakebridge.reconcile.trigger_recon_service.uuid4",
            return_value=recon_id,
        ),
        patch(
            "databricks.labs.lakebridge.reconcile.recon_capture.ReconCapture._generate_recon_main_id",
            return_value=33333333333,
        ),
        pytest.raises(ReconciliationException, match=recon_id.hex),
    ):
        mock_datetime.now.return_value = MOCK_TIMESTAMP
        recon_datetime.now.return_value = MOCK_TIMESTAMP
        TriggerReconService.trigger_recon(mock_workspace_client, spark, table_recon, reconcile_config_exception)

    expected_remorph_recon = spark.createDataFrame(
        data=[
            (
                33333333333,
                recon_id.hex,
                "Snowflake",
                ("org", "data", "supplier"),
                ("org", "data", "target_supplier"),
                "schema",
                "reconcile",
                MOCK_TIMESTAMP,
                MOCK_TIMESTAMP,
            )
        ],
        schema=recon_schema,
    )
    expected_remorph_recon_metrics = spark.createDataFrame(
        data=[
            (
                33333333333,
                (0, 0, None, None, None),
                (
                    False,
                    "remorph",
                    "Runtime exception occurred while fetching schema using (org, data, supplier) : Mock Exception",
                ),
                MOCK_TIMESTAMP,
            )
        ],
        schema=metrics_schema,
    )
    expected_remorph_recon_details = spark.createDataFrame(data=[], schema=details_schema)

    assertDataFrameEqual(
        spark.sql(f"SELECT * FROM {catalog}.{schema}.MAIN"), expected_remorph_recon, ignoreNullable=True
    )
    assertDataFrameEqual(
        spark.sql(f"SELECT * FROM {catalog}.{schema}.METRICS"), expected_remorph_recon_metrics, ignoreNullable=True
    )
    assertDataFrameEqual(
        spark.sql(f"SELECT * FROM {catalog}.{schema}.DETAILS"), expected_remorph_recon_details, ignoreNullable=True
    )


def test_schema_recon_with_general_exception(
    mock_workspace_client, spark, report_tables_schema, mock_for_report_type_schema, tmp_path: Path, recon_id: UUID
):
    recon_schema, metrics_schema, details_schema = report_tables_schema
    table_recon, source, target, reconcile_config_schema = mock_for_report_type_schema
    reconcile_config_schema.source.dialect = "snowflake"
    catalog = reconcile_config_schema.metadata_config.catalog
    schema = reconcile_config_schema.metadata_config.schema
    with (
        patch("databricks.labs.lakebridge.reconcile.trigger_recon_service.datetime") as mock_datetime,
        patch("databricks.labs.lakebridge.reconcile.recon_capture.datetime") as recon_datetime,
        patch("databricks.labs.lakebridge.reconcile.utils.initialise_data_source", return_value=(source, target)),
        patch(
            "databricks.labs.lakebridge.reconcile.trigger_recon_service.uuid4",
            return_value=recon_id,
        ),
        patch(
            "databricks.labs.lakebridge.reconcile.recon_capture.ReconCapture._generate_recon_main_id",
            return_value=33333333333,
        ),
        patch(
            "databricks.labs.lakebridge.reconcile.reconciliation.Reconciliation.reconcile_schema"
        ) as schema_source_mock,
        pytest.raises(ReconciliationException, match=recon_id.hex),
    ):
        schema_source_mock.side_effect = PySparkException("Unknown Error")
        mock_datetime.now.return_value = MOCK_TIMESTAMP
        recon_datetime.now.return_value = MOCK_TIMESTAMP
        TriggerReconService.trigger_recon(mock_workspace_client, spark, table_recon, reconcile_config_schema)

    expected_remorph_recon = spark.createDataFrame(
        data=[
            (
                33333333333,
                recon_id.hex,
                "Snowflake",
                ("org", "data", "supplier"),
                ("org", "data", "target_supplier"),
                "schema",
                "reconcile",
                MOCK_TIMESTAMP,
                MOCK_TIMESTAMP,
            )
        ],
        schema=recon_schema,
    )
    expected_remorph_recon_metrics = spark.createDataFrame(
        data=[
            (
                33333333333,
                (0, 0, None, None, None),
                (
                    False,
                    "remorph",
                    "Unknown Error",
                ),
                MOCK_TIMESTAMP,
            )
        ],
        schema=metrics_schema,
    )
    expected_remorph_recon_details = spark.createDataFrame(data=[], schema=details_schema)

    assertDataFrameEqual(
        spark.sql(f"SELECT * FROM {catalog}.{schema}.MAIN"), expected_remorph_recon, ignoreNullable=True
    )
    assertDataFrameEqual(
        spark.sql(f"SELECT * FROM {catalog}.{schema}.METRICS"), expected_remorph_recon_metrics, ignoreNullable=True
    )
    assertDataFrameEqual(
        spark.sql(f"SELECT * FROM {catalog}.{schema}.DETAILS"), expected_remorph_recon_details, ignoreNullable=True
    )


def test_data_recon_with_general_exception(
    mock_workspace_client, spark, report_tables_schema, mock_for_report_type_schema, tmp_path: Path, recon_id: UUID
):
    recon_schema, metrics_schema, details_schema = report_tables_schema
    table_recon, source, target, reconcile_config = mock_for_report_type_schema
    catalog = reconcile_config.metadata_config.catalog
    schema = reconcile_config.metadata_config.schema
    reconcile_config.source.dialect = "snowflake"
    reconcile_config.report_type = "data"
    with (
        patch("databricks.labs.lakebridge.reconcile.trigger_recon_service.datetime") as mock_datetime,
        patch("databricks.labs.lakebridge.reconcile.recon_capture.datetime") as recon_datetime,
        patch("databricks.labs.lakebridge.reconcile.utils.initialise_data_source", return_value=(source, target)),
        patch(
            "databricks.labs.lakebridge.reconcile.trigger_recon_service.uuid4",
            return_value=recon_id,
        ),
        patch(
            "databricks.labs.lakebridge.reconcile.recon_capture.ReconCapture._generate_recon_main_id",
            return_value=33333333333,
        ),
        patch("databricks.labs.lakebridge.reconcile.reconciliation.Reconciliation.reconcile_data") as data_source_mock,
        pytest.raises(ReconciliationException, match=recon_id.hex),
    ):
        data_source_mock.side_effect = DataSourceRuntimeException("Unknown Error")
        mock_datetime.now.return_value = MOCK_TIMESTAMP
        recon_datetime.now.return_value = MOCK_TIMESTAMP
        TriggerReconService.trigger_recon(mock_workspace_client, spark, table_recon, reconcile_config)

    expected_remorph_recon = spark.createDataFrame(
        data=[
            (
                33333333333,
                recon_id.hex,
                "Snowflake",
                ("org", "data", "supplier"),
                ("org", "data", "target_supplier"),
                "data",
                "reconcile",
                MOCK_TIMESTAMP,
                MOCK_TIMESTAMP,
            )
        ],
        schema=recon_schema,
    )
    expected_remorph_recon_metrics = spark.createDataFrame(
        data=[
            (
                33333333333,
                (3, 3, None, None, None),
                (
                    False,
                    "remorph",
                    "Unknown Error",
                ),
                MOCK_TIMESTAMP,
            )
        ],
        schema=metrics_schema,
    )
    expected_remorph_recon_details = spark.createDataFrame(data=[], schema=details_schema)

    assertDataFrameEqual(
        spark.sql(f"SELECT * FROM {catalog}.{schema}.MAIN"), expected_remorph_recon, ignoreNullable=True
    )
    assertDataFrameEqual(
        spark.sql(f"SELECT * FROM {catalog}.{schema}.METRICS"), expected_remorph_recon_metrics, ignoreNullable=True
    )
    assertDataFrameEqual(
        spark.sql(f"SELECT * FROM {catalog}.{schema}.DETAILS"), expected_remorph_recon_details, ignoreNullable=True
    )


def test_data_recon_with_source_exception(
    mock_workspace_client, spark, report_tables_schema, mock_for_report_type_schema, tmp_path: Path, recon_id: UUID
):
    recon_schema, metrics_schema, details_schema = report_tables_schema
    table_recon, source, target, reconcile_config = mock_for_report_type_schema
    catalog = reconcile_config.metadata_config.catalog
    schema = reconcile_config.metadata_config.schema
    reconcile_config.source.dialect = "snowflake"
    reconcile_config.report_type = "data"
    with (
        patch("databricks.labs.lakebridge.reconcile.trigger_recon_service.datetime") as mock_datetime,
        patch("databricks.labs.lakebridge.reconcile.recon_capture.datetime") as recon_datetime,
        patch("databricks.labs.lakebridge.reconcile.utils.initialise_data_source", return_value=(source, target)),
        patch(
            "databricks.labs.lakebridge.reconcile.trigger_recon_service.uuid4",
            return_value=recon_id,
        ),
        patch(
            "databricks.labs.lakebridge.reconcile.recon_capture.ReconCapture._generate_recon_main_id",
            return_value=33333333333,
        ),
        patch("databricks.labs.lakebridge.reconcile.reconciliation.Reconciliation.reconcile_data") as data_source_mock,
        pytest.raises(ReconciliationException, match=recon_id.hex),
    ):
        data_source_mock.side_effect = DataSourceRuntimeException("Source Runtime Error")
        mock_datetime.now.return_value = MOCK_TIMESTAMP
        recon_datetime.now.return_value = MOCK_TIMESTAMP
        TriggerReconService.trigger_recon(mock_workspace_client, spark, table_recon, reconcile_config)

    expected_remorph_recon = spark.createDataFrame(
        data=[
            (
                33333333333,
                recon_id.hex,
                "Snowflake",
                ("org", "data", "supplier"),
                ("org", "data", "target_supplier"),
                "data",
                "reconcile",
                MOCK_TIMESTAMP,
                MOCK_TIMESTAMP,
            )
        ],
        schema=recon_schema,
    )
    expected_remorph_recon_metrics = spark.createDataFrame(
        data=[
            (
                33333333333,
                (3, 3, None, None, None),
                (
                    False,
                    "remorph",
                    "Source Runtime Error",
                ),
                MOCK_TIMESTAMP,
            )
        ],
        schema=metrics_schema,
    )
    expected_remorph_recon_details = spark.createDataFrame(data=[], schema=details_schema)

    assertDataFrameEqual(
        spark.sql(f"SELECT * FROM {catalog}.{schema}.MAIN"), expected_remorph_recon, ignoreNullable=True
    )
    assertDataFrameEqual(
        spark.sql(f"SELECT * FROM {catalog}.{schema}.METRICS"), expected_remorph_recon_metrics, ignoreNullable=True
    )
    assertDataFrameEqual(
        spark.sql(f"SELECT * FROM {catalog}.{schema}.DETAILS"), expected_remorph_recon_details, ignoreNullable=True
    )


def test_initialise_data_source(spark):
    conn = "test"

    source, target = initialise_data_source(spark, "snowflake", conn)

    assert isinstance(source, SnowflakeDataSource)
    assert isinstance(target, DatabricksDataSource)


def test_recon_for_wrong_report_type(mock_workspace_client, spark, mock_for_report_type_row):
    source, target, table_recon, reconcile_config = mock_for_report_type_row
    reconcile_config.report_type = "ro"
    with (
        patch("databricks.labs.lakebridge.reconcile.trigger_recon_service.datetime") as mock_datetime,
        patch("databricks.labs.lakebridge.reconcile.recon_capture.datetime") as recon_datetime,
        patch("databricks.labs.lakebridge.reconcile.utils.initialise_data_source", return_value=(source, target)),
        patch(
            "databricks.labs.lakebridge.reconcile.trigger_recon_service.uuid4",
            return_value="00112233-4455-6677-8899-aabbccddeeff",
        ),
        patch(
            "databricks.labs.lakebridge.reconcile.recon_capture.ReconCapture._generate_recon_main_id",
            return_value=33333333333,
        ),
        pytest.raises(InvalidInputException),
    ):
        mock_datetime.now.return_value = MOCK_TIMESTAMP
        recon_datetime.now.return_value = MOCK_TIMESTAMP
        TriggerReconService.trigger_recon(mock_workspace_client, spark, table_recon, reconcile_config)


def test_reconcile_data_with_threshold_and_row_report_type(
    spark, normalized_table_conf_with_opts, table_schema_ansi_ansi, query_store, tmp_path: Path
):
    src_schema, tgt_schema = table_schema_ansi_ansi
    source_dataframe_repository = {
        (
            CATALOG,
            SCHEMA,
            query_store.row_queries.source_row_query,
        ): spark.createDataFrame(
            [
                Row(hash_value_recon="a1b", s_nationkey=11, s_suppkey=1),
                Row(hash_value_recon="c2d", s_nationkey=22, s_suppkey=2),
            ]
        ),
        (CATALOG, SCHEMA, query_store.threshold_queries.source_threshold_query): spark.createDataFrame(
            [Row(s_nationkey=11, s_suppkey=1, s_acctbal=100)]
        ),
    }
    source_schema_repository = {(CATALOG, SCHEMA, SRC_TABLE): src_schema}

    target_dataframe_repository = {
        (
            CATALOG,
            SCHEMA,
            query_store.row_queries.target_row_query,
        ): spark.createDataFrame(
            [
                Row(hash_value_recon="a1b", s_nationkey=11, s_suppkey=1),
                Row(hash_value_recon="c2d", s_nationkey=22, s_suppkey=2),
            ]
        ),
        (CATALOG, SCHEMA, query_store.threshold_queries.target_threshold_query): spark.createDataFrame(
            [Row(s_nationkey=11, s_suppkey=1, s_acctbal=110)]
        ),
        (CATALOG, SCHEMA, query_store.threshold_queries.threshold_comparison_query): spark.createDataFrame(
            [
                Row(
                    s_acctbal_source=100,
                    s_acctbal_databricks=110,
                    s_acctbal_match="Warning",
                    s_nationkey_source=11,
                    s_suppkey_source=1,
                )
            ]
        ),
    }

    target_schema_repository = {(CATALOG, SCHEMA, TGT_TABLE): tgt_schema}
    database_config = DatabaseConfig(
        source_catalog=CATALOG,
        source_schema=SCHEMA,
        target_catalog=CATALOG,
        target_schema=SCHEMA,
    )
    schema_comparator = SchemaCompare(spark)
    source = MockDataSource(source_dataframe_repository, source_schema_repository)
    target = MockDataSource(target_dataframe_repository, target_schema_repository)

    actual = Reconciliation(
        source,
        target,
        database_config,
        "row",
        schema_comparator,
        get_dialect("databricks"),
        spark,
        ReconcileMetadataConfig(),
        FakeReconIntermediatePersist(),
    ).reconcile_data(normalized_table_conf_with_opts, src_schema, tgt_schema)

    assert actual.mismatch_count == 0
    assert actual.missing_in_src_count == 0
    assert actual.missing_in_tgt_count == 0
    assert actual.threshold_output.threshold_df is None
    assert actual.threshold_output.threshold_mismatch_count == 0


@patch('databricks.labs.lakebridge.reconcile.recon_capture.generate_final_reconcile_output')
def test_recon_output_without_exception(mock_gen_final_recon_output):
    mock_workspace_client = MagicMock()
    spark = MagicMock()
    mock_table_recon = MagicMock()
    mock_gen_final_recon_output.return_value = ReconcileOutput(
        recon_id="00112233-4455-6677-8899-aabbccddeeff",
        results=[
            ReconcileTableOutput(
                target_table_name="supplier",
                source_table_name="target_supplier",
                status=StatusOutput(
                    row=True,
                    column=True,
                    schema=True,
                ),
                exception_message=None,
            )
        ],
    )
    reconcile_config = ReconcileConfig(
        report_type="all",
        source=SourceConnectionConfig(
            dialect="snowflake",
            catalog=CATALOG,
            schema=SCHEMA,
            uc_connection_name="remorph_snowflake",
        ),
        target=TargetConnectionConfig(
            catalog=CATALOG,
            schema=SCHEMA,
        ),
        metadata_config=ReconcileMetadataConfig(),
    )

    try:
        TriggerReconService.trigger_recon(
            mock_workspace_client,
            spark,
            mock_table_recon,
            reconcile_config,
        )
    except ReconciliationException as e:
        msg = f"An exception {e} was raised when it should not have been"
        pytest.fail(msg)
