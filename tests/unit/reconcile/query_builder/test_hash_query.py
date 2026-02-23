from databricks.labs.lakebridge.transpiler.sqlglot.dialect_utils import get_dialect
from databricks.labs.lakebridge.reconcile.query_builder.hash_query import HashQueryBuilder
from databricks.labs.lakebridge.reconcile.recon_config import Filters, ColumnMapping, Transformation, Table
from databricks.labs.lakebridge.reconcile.normalize_recon_config_service import NormalizeReconConfigService
from tests.conftest import tsql_schema_fixture_factory, ansi_schema_fixture_factory


def test_hash_query_builder_for_snowflake_src(
    snowflake_table_conf_with_opts,
    table_schema_oracle_ansi,
    fake_oracle_datasource,
    fake_databricks_datasource,
):
    src_schema, tgt_schema = table_schema_oracle_ansi
    src_actual = HashQueryBuilder(
        snowflake_table_conf_with_opts,
        src_schema,
        "source",
        get_dialect("snowflake"),
        fake_oracle_datasource,
    ).build_query(report_type="data")
    src_expected = (
        "SELECT LOWER(SHA2(TRIM(\"s_address\") || TRIM(\"s_name\") || COALESCE(TRIM(\"s_nationkey\"), '_null_recon_') || "
        "TRIM(\"s_phone\") || COALESCE(TRIM(\"s_suppkey\"), '_null_recon_'), 256)) AS hash_value_recon, \"s_nationkey\" AS "
        "\"s_nationkey\", "
        "\"s_suppkey\" AS \"s_suppkey\" FROM :tbl WHERE \"s_name\" = 't' AND \"s_address\" = 'a'"
    )

    tgt_actual = HashQueryBuilder(
        snowflake_table_conf_with_opts,
        tgt_schema,
        "target",
        get_dialect("databricks"),
        fake_databricks_datasource,
    ).build_query(report_type="data")
    tgt_expected = (
        "SELECT LOWER(SHA2(TRIM(`s_address_t`) || TRIM(`s_name`) || COALESCE(TRIM(`s_nationkey_t`), '_null_recon_') || "
        "TRIM(`s_phone_t`) || COALESCE(TRIM(`s_suppkey_t`), '_null_recon_'), 256)) AS hash_value_recon, `s_nationkey_t` AS "
        "`s_nationkey`, "
        "`s_suppkey_t` AS `s_suppkey` FROM :tbl WHERE s_name = 't' AND s_address_t = 'a'"
    )

    assert src_actual == src_expected
    assert tgt_actual == tgt_expected


def test_hash_query_builder_for_oracle_src(
    table_conf, table_schema_oracle_ansi, fake_oracle_datasource, fake_databricks_datasource
):
    schema, _ = table_schema_oracle_ansi
    table_conf = table_conf(
        join_columns=["`s_suppkey`", "`s_nationkey`"],
        filters=Filters(source="\"s_nationkey\"=1"),
        column_mapping=[ColumnMapping(source_name="`s_nationkey`", target_name="`s_nationkey`")],
    )
    src_actual = HashQueryBuilder(
        table_conf, schema, "source", get_dialect("oracle"), fake_oracle_datasource
    ).build_query(report_type="all")
    src_expected = (
        "SELECT LOWER(DBMS_CRYPTO.HASH(RAWTOHEX(COALESCE(TRIM(\"s_acctbal\"), '_null_recon_') || COALESCE(TRIM("
        "\"s_address\"), '_null_recon_') || "
        "COALESCE(TRIM(\"s_comment\"), '_null_recon_') || COALESCE(TRIM(\"s_name\"), '_null_recon_') || COALESCE(TRIM("
        "\"s_nationkey\"), '_null_recon_') || COALESCE(TRIM(\"s_phone\"), '_null_recon_') || COALESCE(TRIM(\"s_suppkey\"), "
        "'_null_recon_')), 2)) AS hash_value_recon, \"s_nationkey\" AS \"s_nationkey\", "
        "\"s_suppkey\" AS \"s_suppkey\" FROM :tbl WHERE \"s_nationkey\" = 1"
    )

    tgt_actual = HashQueryBuilder(
        table_conf, schema, "target", get_dialect("databricks"), fake_databricks_datasource
    ).build_query(report_type="all")
    tgt_expected = (
        "SELECT LOWER(SHA2(COALESCE(TRIM(`s_acctbal`), '_null_recon_') || COALESCE(TRIM(`s_address`), "
        "'_null_recon_') || COALESCE(TRIM("
        "`s_comment`), '_null_recon_') || COALESCE(TRIM(`s_name`), '_null_recon_') || COALESCE(TRIM(`s_nationkey`), "
        "'_null_recon_') || COALESCE(TRIM(`s_phone`), "
        "'_null_recon_') || COALESCE(TRIM(`s_suppkey`), '_null_recon_'), 256)) AS hash_value_recon, `s_nationkey` AS "
        "`s_nationkey`, `s_suppkey` "
        "AS `s_suppkey` FROM :tbl"
    )

    assert src_actual == src_expected
    assert tgt_actual == tgt_expected


def test_hash_query_builder_for_databricks_src(
    table_conf, table_schema_oracle_ansi, normalized_column_mapping, fake_databricks_datasource
):
    table_conf = table_conf(
        join_columns=["`s_suppkey`"],
        column_mapping=normalized_column_mapping,
        filters=Filters(target="`s_nationkey_t`=1"),
    )
    sch, sch_with_alias = table_schema_oracle_ansi
    src_actual = HashQueryBuilder(
        table_conf, sch, "source", get_dialect("databricks"), fake_databricks_datasource
    ).build_query(report_type="data")
    src_expected = (
        "SELECT LOWER(SHA2(COALESCE(TRIM(`s_acctbal`), '_null_recon_') || COALESCE(TRIM(`s_address`), '_null_recon_') || "
        "COALESCE(TRIM(`s_comment`), '_null_recon_') || COALESCE(TRIM(`s_name`), '_null_recon_') || COALESCE(TRIM("
        "`s_nationkey`), '_null_recon_') || COALESCE(TRIM("
        "`s_phone`), '_null_recon_') || COALESCE(TRIM(`s_suppkey`), '_null_recon_'), 256)) AS hash_value_recon, `s_suppkey` "
        "AS `s_suppkey` FROM :tbl"
    )

    tgt_actual = HashQueryBuilder(
        table_conf, sch_with_alias, "target", get_dialect("databricks"), fake_databricks_datasource
    ).build_query(report_type="data")
    tgt_expected = (
        "SELECT LOWER(SHA2(COALESCE(TRIM(`s_acctbal_t`), '_null_recon_') || COALESCE(TRIM(`s_address_t`), "
        "'_null_recon_') || COALESCE(TRIM("
        "`s_comment_t`), '_null_recon_') || COALESCE(TRIM(`s_name`), '_null_recon_') || COALESCE(TRIM(`s_nationkey_t`), "
        "'_null_recon_') || COALESCE(TRIM(`s_phone_t`), "
        "'_null_recon_') || COALESCE(TRIM(`s_suppkey_t`), '_null_recon_'), 256)) AS hash_value_recon, `s_suppkey_t` AS "
        "`s_suppkey` FROM :tbl WHERE "
        "`s_nationkey_t` = 1"
    )

    assert src_actual == src_expected
    assert tgt_actual == tgt_expected


def test_hash_query_builder_for_tsql_src(
    tsql_table_conf_with_opts,
    table_schema_tsql_ansi,
    fake_tsql_datasource,
    fake_databricks_datasource,
):
    src_schema, tgt_schema = table_schema_tsql_ansi
    src_actual = HashQueryBuilder(
        tsql_table_conf_with_opts,
        src_schema,
        "source",
        get_dialect("tsql"),
        fake_tsql_datasource,
    ).build_query(report_type="data")
    src_expected = (
        "SELECT LOWER(CONVERT(VARCHAR(64), HASHBYTES('SHA2_256', "
        'CONVERT(VARCHAR(MAX),SUBSTRING([s_address], 1, 11) + UPPER([s_name]) + '
        "COALESCE(TRIM(CAST([s_nationkey] AS VARCHAR(MAX))), '_null_recon_') + "
        "COALESCE(TRIM(CAST([s_phone] AS VARCHAR(MAX))), '_null_recon_') + "
        "COALESCE(TRIM(CAST([s_suppkey] AS VARCHAR(MAX))), '_null_recon_'))), 2)) AS "
        'hash_value_recon, [s_nationkey] AS [s_nationkey], [s_suppkey] AS [s_suppkey] '
        "FROM :tbl WHERE [s_name] = 't' AND [s_address] = 'a'"
    )

    tgt_actual = HashQueryBuilder(
        tsql_table_conf_with_opts,
        tgt_schema,
        "target",
        get_dialect("databricks"),
        fake_databricks_datasource,
    ).build_query(report_type="data")
    print(tgt_actual)
    tgt_expected = (
        'SELECT LOWER(SHA2(SUBSTRING(`s_address_t`, 1, 11) || UPPER(`s_name`) || '
        "COALESCE(TRIM(`s_nationkey_t`), '_null_recon_') || COALESCE(TRIM(`s_phone_t`), "
        "'_null_recon_') || COALESCE(TRIM(`s_suppkey_t`), '_null_recon_'), 256)) AS "
        'hash_value_recon, `s_nationkey_t` AS `s_nationkey`, `s_suppkey_t` AS '
        "`s_suppkey` FROM :tbl WHERE s_name = 't' AND s_address_t = 'a'"
    )

    assert src_actual == src_expected
    assert tgt_actual == tgt_expected


def test_hash_query_builder_without_column_mapping(table_conf, table_schema_oracle_ansi, fake_databricks_datasource):
    table_conf = table_conf(
        join_columns=["`s_suppkey`"],
        filters=Filters(target="`s_nationkey`=1"),
    )
    sch, _ = table_schema_oracle_ansi
    src_actual = HashQueryBuilder(
        table_conf, sch, "source", get_dialect("databricks"), fake_databricks_datasource
    ).build_query(report_type="data")
    src_expected = (
        "SELECT LOWER(SHA2(COALESCE(TRIM(`s_acctbal`), '_null_recon_') || COALESCE(TRIM(`s_address`), '_null_recon_') ||"
        " COALESCE(TRIM(`s_comment`), '_null_recon_') || COALESCE(TRIM(`s_name`), '_null_recon_') || COALESCE(TRIM("
        "`s_nationkey`), '_null_recon_') || COALESCE(TRIM("
        "`s_phone`), '_null_recon_') || COALESCE(TRIM(`s_suppkey`), '_null_recon_'), 256)) AS hash_value_recon, `s_suppkey` "
        "AS `s_suppkey` FROM :tbl"
    )

    tgt_actual = HashQueryBuilder(
        table_conf, sch, "target", get_dialect("databricks"), fake_databricks_datasource
    ).build_query(report_type="data")
    tgt_expected = (
        "SELECT LOWER(SHA2(COALESCE(TRIM(`s_acctbal`), '_null_recon_') || COALESCE(TRIM(`s_address`), "
        "'_null_recon_') || COALESCE(TRIM("
        "`s_comment`), '_null_recon_') || COALESCE(TRIM(`s_name`), '_null_recon_') || COALESCE(TRIM(`s_nationkey`), "
        "'_null_recon_') || COALESCE(TRIM(`s_phone`), "
        "'_null_recon_') || COALESCE(TRIM(`s_suppkey`), '_null_recon_'), 256)) AS hash_value_recon, `s_suppkey` AS "
        "`s_suppkey` FROM :tbl WHERE "
        "`s_nationkey` = 1"
    )

    assert src_actual == src_expected
    assert tgt_actual == tgt_expected


def test_hash_query_builder_without_transformation(
    table_conf, table_schema_oracle_ansi, normalized_column_mapping, fake_databricks_datasource
):
    table_conf = table_conf(
        join_columns=["`s_suppkey`"],
        transformations=[
            Transformation(column_name="`s_address`", source=None, target="trim(`s_address_t`)"),
            Transformation(column_name="`s_name`", source="trim(s_name)", target=None),
            Transformation(column_name="`s_suppkey`", source="trim(s_suppkey)", target=None),
        ],
        column_mapping=normalized_column_mapping,
        filters=Filters(target="s_nationkey_t=1"),
    )
    sch, tgt_sch = table_schema_oracle_ansi
    src_actual = HashQueryBuilder(
        table_conf, sch, "source", get_dialect("databricks"), fake_databricks_datasource
    ).build_query(report_type="data")
    src_expected = (
        "SELECT LOWER(SHA2(COALESCE(TRIM(`s_acctbal`), '_null_recon_') || `s_address` || "
        "COALESCE(TRIM(`s_comment`), '_null_recon_') || TRIM(s_name) || COALESCE(TRIM(`s_nationkey`), '_null_recon_') || "
        "COALESCE(TRIM("
        "`s_phone`), '_null_recon_') || TRIM(s_suppkey), 256)) AS hash_value_recon, TRIM(s_suppkey) AS `s_suppkey` FROM :tbl"
    )

    tgt_actual = HashQueryBuilder(
        table_conf, tgt_sch, "target", get_dialect("databricks"), fake_databricks_datasource
    ).build_query(report_type="data")
    tgt_expected = (
        "SELECT LOWER(SHA2(COALESCE(TRIM(`s_acctbal_t`), '_null_recon_') || TRIM(`s_address_t`) || COALESCE(TRIM("
        "`s_comment_t`), '_null_recon_') || `s_name` || COALESCE(TRIM(`s_nationkey_t`), '_null_recon_') || COALESCE(TRIM("
        "`s_phone_t`), "
        "'_null_recon_') || `s_suppkey_t`, 256)) AS hash_value_recon, `s_suppkey_t` AS `s_suppkey` FROM :tbl WHERE "
        "s_nationkey_t = 1"
    )

    assert src_actual == src_expected
    assert tgt_actual == tgt_expected


def test_hash_query_builder_for_report_type_is_row(
    normalized_table_conf_with_opts, table_schema_oracle_ansi, fake_databricks_datasource
):
    sch, sch_with_alias = table_schema_oracle_ansi
    src_actual = HashQueryBuilder(
        normalized_table_conf_with_opts, sch, "source", get_dialect("databricks"), fake_databricks_datasource
    ).build_query(report_type="row")
    src_expected = (
        "SELECT LOWER(SHA2(TRIM(s_address) || TRIM(s_name) || COALESCE(TRIM(`s_nationkey`), '_null_recon_') || "
        "TRIM(s_phone) || COALESCE(TRIM(`s_suppkey`), '_null_recon_'), 256)) AS hash_value_recon, TRIM(s_address) AS "
        "`s_address`, TRIM(s_name) AS `s_name`, `s_nationkey` AS `s_nationkey`, TRIM(s_phone) "
        "AS `s_phone`, `s_suppkey` AS `s_suppkey` FROM :tbl WHERE s_name = 't' AND "
        "s_address = 'a'"
    )

    tgt_actual = HashQueryBuilder(
        normalized_table_conf_with_opts, sch_with_alias, "target", get_dialect("databricks"), fake_databricks_datasource
    ).build_query(report_type="row")
    tgt_expected = (
        "SELECT LOWER(SHA2(TRIM(s_address_t) || TRIM(s_name) || COALESCE(TRIM(`s_nationkey_t`), '_null_recon_') || "
        "TRIM(s_phone_t) || COALESCE(TRIM(`s_suppkey_t`), '_null_recon_'), 256)) AS hash_value_recon, TRIM(s_address_t) "
        "AS `s_address`, TRIM(s_name) AS `s_name`, `s_nationkey_t` AS `s_nationkey`, "
        "TRIM(s_phone_t) AS `s_phone`, `s_suppkey_t` AS `s_suppkey` FROM :tbl WHERE s_name "
        "= 't' AND s_address_t = 'a'"
    )

    assert src_actual == src_expected
    assert tgt_actual == tgt_expected


def test_config_case_sensitivity(
    table_conf, table_schema_oracle_ansi, normalized_column_mapping, fake_databricks_datasource
):
    table_conf = table_conf(
        select_columns=["`S_SUPPKEY`", "`S_name`", "`S_ADDRESS`", "`S_NATIOnKEY`", "`S_PhONE`", "`S_acctbal`"],
        drop_columns=["`s_Comment`"],
        join_columns=["`S_SUPPKEY`"],
        transformations=[
            Transformation(column_name="`S_ADDRESS`", source=None, target="trim(`s_address_t`)"),
            Transformation(column_name="`S_NAME`", source="trim(`s_name`)", target=None),
            Transformation(column_name="`s_suppKey`", source="trim(`s_suppkey`)", target=None),
        ],
        column_mapping=normalized_column_mapping,
        filters=Filters(target="s_nationkey_t=1"),
    )
    sch, tgt_sch = table_schema_oracle_ansi
    src_actual = HashQueryBuilder(
        table_conf, sch, "source", get_dialect("databricks"), fake_databricks_datasource
    ).build_query(report_type="data")
    src_expected = (
        "SELECT LOWER(SHA2(COALESCE(TRIM(`s_acctbal`), '_null_recon_') || `s_address` || "
        "TRIM(`s_name`) || COALESCE(TRIM(`s_nationkey`), '_null_recon_') || COALESCE(TRIM("
        "`s_phone`), '_null_recon_') || TRIM(`s_suppkey`), 256)) AS hash_value_recon, TRIM(`s_suppkey`) AS `s_suppkey` FROM :tbl"
    )

    tgt_actual = HashQueryBuilder(
        table_conf, tgt_sch, "target", get_dialect("databricks"), fake_databricks_datasource
    ).build_query(report_type="data")
    tgt_expected = (
        "SELECT LOWER(SHA2(COALESCE(TRIM(`s_acctbal_t`), '_null_recon_') || TRIM(`s_address_t`) || `s_name` || "
        "COALESCE(TRIM("
        "`s_nationkey_t`), '_null_recon_') || COALESCE(TRIM(`s_phone_t`), '_null_recon_') || `s_suppkey_t`, "
        "256)) AS hash_value_recon, `s_suppkey_t` AS "
        "`s_suppkey` FROM :tbl WHERE s_nationkey_t = 1"
    )

    assert src_actual == src_expected
    assert tgt_actual == tgt_expected


def test_hash_query_builder_sort_column(
    fake_tsql_datasource,
    fake_databricks_datasource,
):
    """Test column ordering for T-SQL when month and month_num columns are present."""
    src_schema = [
        tsql_schema_fixture_factory("id", "number"),
        tsql_schema_fixture_factory("month_num", "number"),
        tsql_schema_fixture_factory("month", "number"),
        tsql_schema_fixture_factory("year", "number"),
        tsql_schema_fixture_factory("revenue", "number"),
    ]

    tgt_schema = [
        ansi_schema_fixture_factory("id", "number"),
        ansi_schema_fixture_factory("month", "number"),
        ansi_schema_fixture_factory("month_num", "number"),
        ansi_schema_fixture_factory("year", "number"),
        ansi_schema_fixture_factory("revenue", "number"),
    ]

    # Create table configuration
    table_conf = Table(
        source_name="sales_report",
        target_name="sales_report",
        join_columns=["id"],
        select_columns=["id", "month", "month_num", "year", "revenue"],
    )

    # Normalize the configuration
    normalize_service = NormalizeReconConfigService(fake_tsql_datasource, fake_databricks_datasource)
    normalized_conf = normalize_service.normalize_recon_table_config(table_conf)

    # Build source query (T-SQL)
    src_actual = HashQueryBuilder(
        normalized_conf,
        src_schema,
        "source",
        get_dialect("tsql"),
        fake_tsql_datasource,
    ).build_query(report_type="data")

    # Build target query (Databricks)
    tgt_actual = HashQueryBuilder(
        normalized_conf,
        tgt_schema,
        "target",
        get_dialect("databricks"),
        fake_databricks_datasource,
    ).build_query(report_type="data")

    # Verify columns are in alphabetical order: id, month, month_num, revenue, year
    src_expected = (
        "SELECT LOWER(CONVERT(VARCHAR(64), HASHBYTES('SHA2_256', "
        "CONVERT(VARCHAR(MAX),COALESCE(TRIM(CAST([id] AS VARCHAR(MAX))), '_null_recon_') + "
        "COALESCE(TRIM(CAST([month] AS VARCHAR(MAX))), '_null_recon_') + "
        "COALESCE(TRIM(CAST([month_num] AS VARCHAR(MAX))), '_null_recon_') + "
        "COALESCE(TRIM(CAST([revenue] AS VARCHAR(MAX))), '_null_recon_') + "
        "COALESCE(TRIM(CAST([year] AS VARCHAR(MAX))), '_null_recon_'))), 2)) AS "
        "hash_value_recon, [id] AS [id] FROM :tbl"
    )

    tgt_expected = (
        "SELECT LOWER(SHA2(COALESCE(TRIM(`id`), '_null_recon_') || "
        "COALESCE(TRIM(`month`), '_null_recon_') || "
        "COALESCE(TRIM(`month_num`), '_null_recon_') || "
        "COALESCE(TRIM(`revenue`), '_null_recon_') || "
        "COALESCE(TRIM(`year`), '_null_recon_'), 256)) AS "
        "hash_value_recon, `id` AS `id` FROM :tbl"
    )

    assert src_actual == src_expected
    assert tgt_actual == tgt_expected
