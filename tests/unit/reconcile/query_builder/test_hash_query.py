import sqlglot

from databricks.labs.lakebridge.reconcile.normalize_recon_config_service import NormalizeReconConfigService
from databricks.labs.lakebridge.reconcile.query_builder.hash_query import HashQueryBuilder
from databricks.labs.lakebridge.reconcile.recon_config import (
    ColumnMapping,
    Filters,
    Table,
    Transformation,
)
from databricks.labs.lakebridge.transpiler.sqlglot.dialect_utils import get_dialect
from tests.conftest import (
    FakeDataSource,
    ansi_schema_fixture_factory,
    make_column_transformer,
    redshift_schema_fixture_factory,
    tsql_schema_fixture_factory,
)


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
        make_column_transformer(
            src_schema, get_dialect("snowflake"), fake_oracle_datasource, snowflake_table_conf_with_opts
        ),
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
        make_column_transformer(
            tgt_schema, get_dialect("databricks"), fake_databricks_datasource, snowflake_table_conf_with_opts
        ),
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
        table_conf,
        schema,
        "source",
        get_dialect("oracle"),
        fake_oracle_datasource,
        make_column_transformer(schema, get_dialect("oracle"), fake_oracle_datasource, table_conf),
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
        table_conf,
        schema,
        "target",
        get_dialect("databricks"),
        fake_databricks_datasource,
        make_column_transformer(schema, get_dialect("databricks"), fake_databricks_datasource, table_conf),
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
        table_conf,
        sch,
        "source",
        get_dialect("databricks"),
        fake_databricks_datasource,
        make_column_transformer(sch, get_dialect("databricks"), fake_databricks_datasource, table_conf),
    ).build_query(report_type="data")
    src_expected = (
        "SELECT LOWER(SHA2(COALESCE(TRIM(`s_acctbal`), '_null_recon_') || COALESCE(TRIM(`s_address`), '_null_recon_') || "
        "COALESCE(TRIM(`s_comment`), '_null_recon_') || COALESCE(TRIM(`s_name`), '_null_recon_') || COALESCE(TRIM("
        "`s_nationkey`), '_null_recon_') || COALESCE(TRIM("
        "`s_phone`), '_null_recon_') || COALESCE(TRIM(`s_suppkey`), '_null_recon_'), 256)) AS hash_value_recon, `s_suppkey` "
        "AS `s_suppkey` FROM :tbl"
    )

    tgt_actual = HashQueryBuilder(
        table_conf,
        sch_with_alias,
        "target",
        get_dialect("databricks"),
        fake_databricks_datasource,
        make_column_transformer(sch_with_alias, get_dialect("databricks"), fake_databricks_datasource, table_conf),
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


def test_hash_query_builder_for_redshift_src(
    redshift_table_conf_with_opts,
    table_schema_redshift_ansi,
    fake_redshift_datasource,
    fake_databricks_datasource,
):
    src_schema, tgt_schema = table_schema_redshift_ansi
    src_actual = HashQueryBuilder(
        redshift_table_conf_with_opts,
        src_schema,
        "source",
        get_dialect("redshift"),
        fake_redshift_datasource,
        make_column_transformer(
            src_schema, get_dialect("redshift"), fake_redshift_datasource, redshift_table_conf_with_opts
        ),
    ).build_query(report_type="data")
    src_expected = (
        "SELECT LOWER(SHA2(TRIM(\"s_address\") || TRIM(\"s_name\") || COALESCE(TRIM(\"s_nationkey\"), '_null_recon_') || "
        "TRIM(\"s_phone\") || COALESCE(TRIM(\"s_suppkey\"), '_null_recon_'), 256)) AS hash_value_recon, \"s_nationkey\" AS "
        "\"s_nationkey\", "
        "\"s_suppkey\" AS \"s_suppkey\" FROM %(tbl)s WHERE \"s_name\" = 't' AND \"s_address\" = 'a'"
    )

    tgt_actual = HashQueryBuilder(
        redshift_table_conf_with_opts,
        tgt_schema,
        "target",
        get_dialect("databricks"),
        fake_databricks_datasource,
        make_column_transformer(
            tgt_schema, get_dialect("databricks"), fake_databricks_datasource, redshift_table_conf_with_opts
        ),
    ).build_query(report_type="data")
    tgt_expected = (
        "SELECT LOWER(SHA2(TRIM(`s_address_t`) || TRIM(`s_name`) || COALESCE(TRIM(`s_nationkey_t`), '_null_recon_') || "
        "TRIM(`s_phone_t`) || COALESCE(TRIM(`s_suppkey_t`), '_null_recon_'), 256)) AS hash_value_recon, `s_nationkey_t` AS "
        "`s_nationkey`, "
        "`s_suppkey_t` AS `s_suppkey` FROM :tbl WHERE s_name = 't' AND s_address_t = 'a'"
    )

    assert src_actual == src_expected
    assert tgt_actual == tgt_expected


def test_hash_query_builder_user_hash_expression_overrides_dialect_default(
    teradata_table_conf_with_opts,
    table_schema_teradata_ansi,
    fake_teradata_datasource,
    fake_databricks_datasource,
):
    src_schema, tgt_schema = table_schema_teradata_ansi
    src_actual = HashQueryBuilder(
        teradata_table_conf_with_opts,
        src_schema,
        "source",
        get_dialect("teradata"),
        fake_teradata_datasource,
        make_column_transformer(
            src_schema, get_dialect("teradata"), fake_teradata_datasource, teradata_table_conf_with_opts
        ),
        hash_expression_override="my_db.my_sha256({})",
    ).build_query(report_type="data")
    src_expected = (
        'SELECT LOWER(my_db.my_sha256(TRIM("s_address") || TRIM("s_name") || '
        "COALESCE(TRIM(\"s_nationkey\"), '_null_recon_') || TRIM(\"s_phone\") || "
        "COALESCE(TRIM(\"s_suppkey\"), '_null_recon_'))) AS hash_value_recon, "
        '"s_nationkey" AS "s_nationkey", "s_suppkey" AS "s_suppkey" FROM :tbl '
        "WHERE \"s_name\" = 't' AND \"s_address\" = 'a'"
    )

    tgt_actual = HashQueryBuilder(
        teradata_table_conf_with_opts,
        tgt_schema,
        "target",
        get_dialect("databricks"),
        fake_databricks_datasource,
        make_column_transformer(
            tgt_schema, get_dialect("databricks"), fake_databricks_datasource, teradata_table_conf_with_opts
        ),
        hash_expression_override="sha2({}, 256)",
    ).build_query(report_type="data")
    tgt_expected = (
        "SELECT LOWER(SHA2(TRIM(`s_address_t`) || TRIM(`s_name`) || "
        "COALESCE(TRIM(`s_nationkey_t`), '_null_recon_') || TRIM(`s_phone_t`) || "
        "COALESCE(TRIM(`s_suppkey_t`), '_null_recon_'), 256)) AS hash_value_recon, "
        "`s_nationkey_t` AS `s_nationkey`, `s_suppkey_t` AS `s_suppkey` "
        "FROM :tbl WHERE s_name = 't' AND s_address_t = 'a'"
    )

    assert src_actual == src_expected
    assert tgt_actual == tgt_expected


def test_hash_query_builder_user_hash_expression_only_source(
    teradata_table_conf_with_opts,
    table_schema_teradata_ansi,
    fake_teradata_datasource,
    fake_databricks_datasource,
):
    src_schema, tgt_schema = table_schema_teradata_ansi
    src_actual = HashQueryBuilder(
        teradata_table_conf_with_opts,
        src_schema,
        "source",
        get_dialect("teradata"),
        fake_teradata_datasource,
        make_column_transformer(
            src_schema, get_dialect("teradata"), fake_teradata_datasource, teradata_table_conf_with_opts
        ),
        hash_expression_override="my_db.my_sha256({})",
    ).build_query(report_type="data")
    assert "my_db.my_sha256(" in src_actual

    tgt_actual = HashQueryBuilder(
        teradata_table_conf_with_opts,
        tgt_schema,
        "target",
        get_dialect("databricks"),
        fake_databricks_datasource,
        make_column_transformer(
            tgt_schema, get_dialect("databricks"), fake_databricks_datasource, teradata_table_conf_with_opts
        ),
    ).build_query(report_type="data")
    # Without an override, the target falls back to the dialect default (Databricks SHA2).
    assert "SHA2(" in tgt_actual
    assert "my_db.my_sha256(" not in tgt_actual


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
        make_column_transformer(src_schema, get_dialect("tsql"), fake_tsql_datasource, tsql_table_conf_with_opts),
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
        make_column_transformer(
            tgt_schema, get_dialect("databricks"), fake_databricks_datasource, tsql_table_conf_with_opts
        ),
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
        table_conf,
        sch,
        "source",
        get_dialect("databricks"),
        fake_databricks_datasource,
        make_column_transformer(sch, get_dialect("databricks"), fake_databricks_datasource, table_conf),
    ).build_query(report_type="data")
    src_expected = (
        "SELECT LOWER(SHA2(COALESCE(TRIM(`s_acctbal`), '_null_recon_') || COALESCE(TRIM(`s_address`), '_null_recon_') ||"
        " COALESCE(TRIM(`s_comment`), '_null_recon_') || COALESCE(TRIM(`s_name`), '_null_recon_') || COALESCE(TRIM("
        "`s_nationkey`), '_null_recon_') || COALESCE(TRIM("
        "`s_phone`), '_null_recon_') || COALESCE(TRIM(`s_suppkey`), '_null_recon_'), 256)) AS hash_value_recon, `s_suppkey` "
        "AS `s_suppkey` FROM :tbl"
    )

    tgt_actual = HashQueryBuilder(
        table_conf,
        sch,
        "target",
        get_dialect("databricks"),
        fake_databricks_datasource,
        make_column_transformer(sch, get_dialect("databricks"), fake_databricks_datasource, table_conf),
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
        table_conf,
        sch,
        "source",
        get_dialect("databricks"),
        fake_databricks_datasource,
        make_column_transformer(sch, get_dialect("databricks"), fake_databricks_datasource, table_conf),
    ).build_query(report_type="data")
    src_expected = (
        "SELECT LOWER(SHA2(COALESCE(TRIM(`s_acctbal`), '_null_recon_') || `s_address` || "
        "COALESCE(TRIM(`s_comment`), '_null_recon_') || TRIM(s_name) || COALESCE(TRIM(`s_nationkey`), '_null_recon_') || "
        "COALESCE(TRIM("
        "`s_phone`), '_null_recon_') || TRIM(s_suppkey), 256)) AS hash_value_recon, TRIM(s_suppkey) AS `s_suppkey` FROM :tbl"
    )

    tgt_actual = HashQueryBuilder(
        table_conf,
        tgt_sch,
        "target",
        get_dialect("databricks"),
        fake_databricks_datasource,
        make_column_transformer(tgt_sch, get_dialect("databricks"), fake_databricks_datasource, table_conf),
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
        normalized_table_conf_with_opts,
        sch,
        "source",
        get_dialect("databricks"),
        fake_databricks_datasource,
        make_column_transformer(
            sch, get_dialect("databricks"), fake_databricks_datasource, normalized_table_conf_with_opts
        ),
    ).build_query(report_type="row")
    src_expected = (
        "SELECT LOWER(SHA2(TRIM(s_address) || TRIM(s_name) || COALESCE(TRIM(`s_nationkey`), '_null_recon_') || "
        "TRIM(s_phone) || COALESCE(TRIM(`s_suppkey`), '_null_recon_'), 256)) AS hash_value_recon, TRIM(s_address) AS "
        "`s_address`, TRIM(s_name) AS `s_name`, `s_nationkey` AS `s_nationkey`, TRIM(s_phone) "
        "AS `s_phone`, `s_suppkey` AS `s_suppkey` FROM :tbl WHERE s_name = 't' AND "
        "s_address = 'a'"
    )

    tgt_actual = HashQueryBuilder(
        normalized_table_conf_with_opts,
        sch_with_alias,
        "target",
        get_dialect("databricks"),
        fake_databricks_datasource,
        make_column_transformer(
            sch_with_alias, get_dialect("databricks"), fake_databricks_datasource, normalized_table_conf_with_opts
        ),
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
        table_conf,
        sch,
        "source",
        get_dialect("databricks"),
        fake_databricks_datasource,
        make_column_transformer(sch, get_dialect("databricks"), fake_databricks_datasource, table_conf),
    ).build_query(report_type="data")
    src_expected = (
        "SELECT LOWER(SHA2(COALESCE(TRIM(`s_acctbal`), '_null_recon_') || `s_address` || "
        "TRIM(`s_name`) || COALESCE(TRIM(`s_nationkey`), '_null_recon_') || COALESCE(TRIM("
        "`s_phone`), '_null_recon_') || TRIM(`s_suppkey`), 256)) AS hash_value_recon, TRIM(`s_suppkey`) AS `s_suppkey` FROM :tbl"
    )

    tgt_actual = HashQueryBuilder(
        table_conf,
        tgt_sch,
        "target",
        get_dialect("databricks"),
        fake_databricks_datasource,
        make_column_transformer(tgt_sch, get_dialect("databricks"), fake_databricks_datasource, table_conf),
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
        make_column_transformer(src_schema, get_dialect("tsql"), fake_tsql_datasource, normalized_conf),
    ).build_query(report_type="data")

    # Build target query (Databricks)
    tgt_actual = HashQueryBuilder(
        normalized_conf,
        tgt_schema,
        "target",
        get_dialect("databricks"),
        fake_databricks_datasource,
        make_column_transformer(tgt_schema, get_dialect("databricks"), fake_databricks_datasource, normalized_conf),
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


def test_hash_query_builder_tsql_date_time_columns(
    fake_tsql_datasource: FakeDataSource,
    fake_databricks_datasource: FakeDataSource,
) -> None:
    src_schema = [
        tsql_schema_fixture_factory("id", "number"),
        tsql_schema_fixture_factory("created_date", "date"),
        tsql_schema_fixture_factory("event_time", "time"),
        tsql_schema_fixture_factory("updated_at", "datetime"),
    ]

    table_conf = Table(
        source_name="events",
        target_name="events",
        join_columns=["id"],
        select_columns=["id", "created_date", "event_time", "updated_at"],
    )

    normalize_service = NormalizeReconConfigService(fake_tsql_datasource, fake_databricks_datasource)
    normalized_conf = normalize_service.normalize_recon_table_config(table_conf)

    src_actual = HashQueryBuilder(
        normalized_conf,
        src_schema,
        "source",
        get_dialect("tsql"),
        fake_tsql_datasource,
        make_column_transformer(src_schema, get_dialect("tsql"), fake_tsql_datasource, normalized_conf),
    ).build_query(report_type="data")

    src_expected = (
        "SELECT LOWER(CONVERT(VARCHAR(64), HASHBYTES('SHA2_256', "
        "CONVERT(VARCHAR(MAX),COALESCE(CONVERT(VARCHAR(10), [created_date], 101), '1900-01-01') + "
        "COALESCE(CONVERT(VARCHAR(12), [event_time], 108), '00:00:00') + "
        "COALESCE(TRIM(CAST([id] AS VARCHAR(MAX))), '_null_recon_') + "
        "COALESCE(CONVERT(VARCHAR(23), [updated_at], 120), '1900-01-01 00:00:00'))), 2)) AS "
        "hash_value_recon, [id] AS [id] FROM :tbl"
    )

    assert src_actual == src_expected


def test_build_query_from_expression_splices_postgres_pyformat_placeholder(
    redshift_table_conf_with_opts,
    table_schema_redshift_ansi,
    fake_redshift_datasource,
):
    """A Redshift-source query renders the placeholder as pyformat ``%(tbl)s``; passing a
    ``from_expression`` splices the concrete subquery in and leaves no placeholder behind."""
    src_schema, _ = table_schema_redshift_ansi
    query = HashQueryBuilder(
        redshift_table_conf_with_opts,
        src_schema,
        "source",
        get_dialect("redshift"),
        fake_redshift_datasource,
        make_column_transformer(
            src_schema, get_dialect("redshift"), fake_redshift_datasource, redshift_table_conf_with_opts
        ),
    ).build_query(report_type="data", from_expression="(SELECT * FROM t WHERE x = 1) _sub")

    assert "(SELECT * FROM t WHERE x = 1) _sub" in query
    assert "%(tbl)s" not in query
    assert ":tbl" not in query


def test_build_query_from_expression_splices_spark_placeholder(
    snowflake_table_conf_with_opts,
    table_schema_oracle_ansi,
    fake_databricks_datasource,
):
    """A Databricks-target query renders the placeholder as ``:tbl``; a ``from_expression``
    is spliced in raw (not re-parsed) so a hand-built subquery survives verbatim."""
    _, tgt_schema = table_schema_oracle_ansi
    query = HashQueryBuilder(
        snowflake_table_conf_with_opts,
        tgt_schema,
        "target",
        get_dialect("databricks"),
        fake_databricks_datasource,
        make_column_transformer(
            tgt_schema, get_dialect("databricks"), fake_databricks_datasource, snowflake_table_conf_with_opts
        ),
    ).build_query(report_type="data", from_expression="(SELECT * FROM t WHERE x = 1) _sub")

    assert "(SELECT * FROM t WHERE x = 1) _sub" in query
    assert ":tbl" not in query
    assert "%(tbl)s" not in query


def test_build_query_without_from_expression_keeps_placeholder(
    redshift_table_conf_with_opts,
    table_schema_redshift_ansi,
    fake_redshift_datasource,
):
    """The normal path leaves the placeholder for the connector's ``read_data`` to fill."""
    src_schema, _ = table_schema_redshift_ansi
    query = HashQueryBuilder(
        redshift_table_conf_with_opts,
        src_schema,
        "source",
        get_dialect("redshift"),
        fake_redshift_datasource,
        make_column_transformer(
            src_schema, get_dialect("redshift"), fake_redshift_datasource, redshift_table_conf_with_opts
        ),
    ).build_query(report_type="data")

    assert "%(tbl)s" in query


def _projection_aliases(sql: str, dialect: str) -> list[str]:
    """Output column names (aliases) of a hash query's SELECT, minus the hash column."""
    parsed = sqlglot.parse_one(sql, read=dialect)
    return [p.alias_or_name for p in parsed.expressions if p.alias_or_name != "hash_value_recon"]


def test_hash_query_permuting_column_mapping_keeps_layers_aligned():
    """Regression for review finding B1 + the pre-existing hash-order desync.

    A ``column_mapping`` that PERMUTES alphabetical order (source ``alpha`` -> target
    ``zeta``, source ``beta`` -> target ``apple``) must not desync the two layers. Before
    the fix the layer-local sort ordered the source side as [alpha, beta, id] but the target
    side as [apple, id, zeta] (= source [beta, id, alpha]); the row hashes diverged (false
    mismatch on every row) and the ``project_all_columns`` Stage-2 projection tripped
    ``capture_mismatch_data_and_columns``' ``source_columns != target_columns`` guard
    (``ColumnMismatchException``). Sorting by the source-side identifier keeps both layers
    in source-canonical order.
    """
    table = Table(
        source_name="t",
        target_name="t",
        join_columns=["id"],
        select_columns=["alpha", "beta"],
        column_mapping=[
            ColumnMapping(source_name="alpha", target_name="zeta"),
            ColumnMapping(source_name="beta", target_name="apple"),
        ],
    )
    src_schema = [redshift_schema_fixture_factory(n, "int") for n in ["id", "alpha", "beta"]]
    tgt_schema = [ansi_schema_fixture_factory(n, "int") for n in ["id", "zeta", "apple"]]
    src_ds = FakeDataSource('"', '"')
    tgt_ds = FakeDataSource("`", "`")

    src_builder = HashQueryBuilder(
        table,
        src_schema,
        "source",
        get_dialect("redshift"),
        src_ds,
        make_column_transformer(src_schema, get_dialect("redshift"), src_ds, table),
    )
    tgt_builder = HashQueryBuilder(
        table,
        tgt_schema,
        "target",
        get_dialect("databricks"),
        tgt_ds,
        make_column_transformer(tgt_schema, get_dialect("databricks"), tgt_ds, table),
    )

    # (1) Row hash: both layers hash the same source-logical columns in the same order.
    src_hash_seq = [table.get_layer_tgt_to_src_col_mapping(c, "source") for c in src_builder.ordered_hash_columns()]
    tgt_hash_seq = [table.get_layer_tgt_to_src_col_mapping(c, "target") for c in tgt_builder.ordered_hash_columns()]
    assert src_hash_seq == tgt_hash_seq == ["alpha", "beta", "id"]

    # (2) Stage-2 projection: identical column lists (order + names) on both sides, so
    # capture_mismatch_data_and_columns does not raise ColumnMismatchException.
    src_sql = src_builder.build_query(report_type="all", project_all_columns=True)
    tgt_sql = tgt_builder.build_query(report_type="all", project_all_columns=True)
    assert (
        _projection_aliases(src_sql, "redshift")
        == _projection_aliases(tgt_sql, "databricks")
        == ["alpha", "beta", "id"]
    )


def test_hash_query_order_preserving_mapping_is_unchanged_by_source_alignment():
    """The source-aligned ordering must be a no-op for order-preserving mappings (the common
    case): a shared-suffix rename keeps the same relative order, so source and target already
    agree and the generated column order is identical to the pre-fix behaviour.
    """
    table = Table(
        source_name="t",
        target_name="t",
        join_columns=["id"],
        select_columns=["alpha", "beta"],
        column_mapping=[
            ColumnMapping(source_name="alpha", target_name="alpha_t"),
            ColumnMapping(source_name="beta", target_name="beta_t"),
        ],
    )
    src_schema = [redshift_schema_fixture_factory(n, "int") for n in ["id", "alpha", "beta"]]
    tgt_schema = [ansi_schema_fixture_factory(n, "int") for n in ["id", "alpha_t", "beta_t"]]
    src_ds = FakeDataSource('"', '"')
    tgt_ds = FakeDataSource("`", "`")

    src_builder = HashQueryBuilder(
        table,
        src_schema,
        "source",
        get_dialect("redshift"),
        src_ds,
        make_column_transformer(src_schema, get_dialect("redshift"), src_ds, table),
    )
    tgt_builder = HashQueryBuilder(
        table,
        tgt_schema,
        "target",
        get_dialect("databricks"),
        tgt_ds,
        make_column_transformer(tgt_schema, get_dialect("databricks"), tgt_ds, table),
    )

    src_sql = src_builder.build_query(report_type="all", project_all_columns=True)
    tgt_sql = tgt_builder.build_query(report_type="all", project_all_columns=True)
    assert (
        _projection_aliases(src_sql, "redshift")
        == _projection_aliases(tgt_sql, "databricks")
        == ["alpha", "beta", "id"]
    )
