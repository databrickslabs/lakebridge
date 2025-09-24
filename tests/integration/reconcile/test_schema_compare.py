import pytest

from databricks.labs.lakebridge.transpiler.sqlglot.dialect_utils import get_dialect
from databricks.labs.lakebridge.reconcile.recon_config import ColumnMapping, Table
from databricks.labs.lakebridge.reconcile.schema_compare import SchemaCompare

from tests.conftest import schema_fixture_factory


def snowflake_databricks_schema():
    src_schema = [
        schema_fixture_factory("col_boolean", "boolean", source_delimiter='"'),
        schema_fixture_factory("col_char", "varchar(1)", source_delimiter='"'),
        schema_fixture_factory("col_varchar", "varchar(16777216)", source_delimiter='"'),
        schema_fixture_factory("col_string", "varchar(16777216)", source_delimiter='"'),
        schema_fixture_factory("col_text", "varchar(16777216)", source_delimiter='"'),
        schema_fixture_factory("col_binary", "binary(8388608)", source_delimiter='"'),
        schema_fixture_factory("col_varbinary", "binary(8388608)", source_delimiter='"'),
        schema_fixture_factory("col_int", "number(38,0)", source_delimiter='"'),
        schema_fixture_factory("col_bigint", "number(38,0)", source_delimiter='"'),
        schema_fixture_factory("col_smallint", "number(38,0)", source_delimiter='"'),
        schema_fixture_factory("col_float", "float", source_delimiter='"'),
        schema_fixture_factory("col_float4", "float", source_delimiter='"'),
        schema_fixture_factory("col_double", "float", source_delimiter='"'),
        schema_fixture_factory("col_real", "float", source_delimiter='"'),
        schema_fixture_factory("col_date", "date", source_delimiter='"'),
        schema_fixture_factory("col_time", "time(9)", source_delimiter='"'),
        schema_fixture_factory("col_timestamp", "timestamp_ntz(9)", source_delimiter='"'),
        schema_fixture_factory("col_timestamp_ltz", "timestamp_ltz(9)", source_delimiter='"'),
        schema_fixture_factory("col_timestamp_ntz", "timestamp_ntz(9)", source_delimiter='"'),
        schema_fixture_factory("col_timestamp_tz", "timestamp_tz(9)", source_delimiter='"'),
        schema_fixture_factory("col_variant", "variant", source_delimiter='"'),
        schema_fixture_factory("col_object", "object", source_delimiter='"'),
        schema_fixture_factory("col_array", "array", source_delimiter='"'),
        schema_fixture_factory("col_array_int", "array", source_delimiter='"'),
        schema_fixture_factory("col_array_float", "array", source_delimiter='"'),
        schema_fixture_factory("col_geography", "geography", source_delimiter='"'),
        schema_fixture_factory("col_num10", "number(10,1)", source_delimiter='"'),
        schema_fixture_factory("col_dec", "number(20,2)", source_delimiter='"'),
        schema_fixture_factory("col_numeric_2", "numeric(38,0)", source_delimiter='"'),
        schema_fixture_factory("col_escaped", "float", source_delimiter='"'),
        schema_fixture_factory("`col Escaped2`", "float", source_delimiter='"'),
        schema_fixture_factory('"col escaped3"', "float", source_delimiter='"'),
        schema_fixture_factory('"col""escaped4"', "float", source_delimiter='"'),
        schema_fixture_factory('"col`escaped5"', "float", source_delimiter='"'),
        schema_fixture_factory('"col `$ EscAped6"', "float", source_delimiter='"'),
        schema_fixture_factory("dummy", "string", source_delimiter='"'),
    ]
    tgt_schema = [
        schema_fixture_factory("col_boolean", "boolean", source_delimiter='`'),
        schema_fixture_factory("char", "string", source_delimiter='`'),
        schema_fixture_factory("col_varchar", "string", source_delimiter='`'),
        schema_fixture_factory("col_string", "string", source_delimiter='`'),
        schema_fixture_factory("col_text", "string", source_delimiter='`'),
        schema_fixture_factory("col_binary", "binary", source_delimiter='`'),
        schema_fixture_factory("col_varbinary", "binary", source_delimiter='`'),
        schema_fixture_factory("col_int", "decimal(38,0)", source_delimiter='`'),
        schema_fixture_factory("col_bigint", "decimal(38,0)", source_delimiter='`'),
        schema_fixture_factory("col_smallint", "decimal(38,0)", source_delimiter='`'),
        schema_fixture_factory("col_float", "double", source_delimiter='`'),
        schema_fixture_factory("col_float4", "double", source_delimiter='`'),
        schema_fixture_factory("col_double", "double", source_delimiter='`'),
        schema_fixture_factory("col_real", "double", source_delimiter='`'),
        schema_fixture_factory("col_date", "date", source_delimiter='`'),
        schema_fixture_factory("col_time", "timestamp", source_delimiter='`'),
        schema_fixture_factory("col_timestamp", "timestamp_ntz", source_delimiter='`'),
        schema_fixture_factory("col_timestamp_ltz", "timestamp", source_delimiter='`'),
        schema_fixture_factory("col_timestamp_ntz", "timestamp_ntz", source_delimiter='`'),
        schema_fixture_factory("col_timestamp_tz", "timestamp", source_delimiter='`'),
        schema_fixture_factory("col_variant", "variant", source_delimiter='`'),
        schema_fixture_factory("col_object", "string", source_delimiter='`'),
        schema_fixture_factory("array_col", "array<string>", source_delimiter='`'),
        schema_fixture_factory("col_array_int", "array<int>", source_delimiter='`'),
        schema_fixture_factory("col_array_float", "array<double>", source_delimiter='`'),
        schema_fixture_factory("col_geography", "string", source_delimiter='`'),
        schema_fixture_factory("col_num10", "decimal(10,1)", source_delimiter='`'),
        schema_fixture_factory("col_dec", "decimal(20,1)", source_delimiter='`'),
        schema_fixture_factory("col_numeric_2", "decimal(38,0)", source_delimiter='`'),
        schema_fixture_factory("col_escaped", "double", source_delimiter='`'),
        schema_fixture_factory("`col Escaped2`", "double", source_delimiter='`'),
        schema_fixture_factory('`col escaped3`', "double", source_delimiter='`'),
        schema_fixture_factory('`col"escaped4`', "double", source_delimiter='`'),
        schema_fixture_factory('`col``escaped5`', "double", source_delimiter='`'),
        schema_fixture_factory('`col ``$ EscAped6`', "double", source_delimiter='`'),
    ]
    return src_schema, tgt_schema


def databricks_databricks_schema():
    src_schema = [
        schema_fixture_factory("col_boolean", "boolean", source_delimiter='`'),
        schema_fixture_factory("col_char", "string", source_delimiter='`'),
        schema_fixture_factory("col_int", "int", source_delimiter='`'),
        schema_fixture_factory("col_string", "string", source_delimiter='`'),
        schema_fixture_factory("col_bigint", "int", source_delimiter='`'),
        schema_fixture_factory("col_num10", "decimal(10,1)", source_delimiter='`'),
        schema_fixture_factory("col_dec", "decimal(20,2)", source_delimiter='`'),
        schema_fixture_factory("col_numeric_2", "decimal(38,0)", source_delimiter='`'),
        schema_fixture_factory("col_escaped", "double", source_delimiter='`'),
        schema_fixture_factory("`col Escaped2`", "double", source_delimiter='`'),
        schema_fixture_factory('`col escaped3`', "double", source_delimiter='`'),
        schema_fixture_factory('`col"escaped4`', "double", source_delimiter='`'),
        schema_fixture_factory('`col``escaped5`', "double", source_delimiter='`'),
        schema_fixture_factory('`col ``$ EscAped6`', "double", source_delimiter='`'),
        schema_fixture_factory("dummy", "string", source_delimiter='`'),
    ]
    tgt_schema = [
        schema_fixture_factory("col_boolean", "boolean", source_delimiter='`'),
        schema_fixture_factory("char", "string", source_delimiter='`'),
        schema_fixture_factory("col_int", "int", source_delimiter='`'),
        schema_fixture_factory("col_string", "string", source_delimiter='`'),
        schema_fixture_factory("col_bigint", "int", source_delimiter='`'),
        schema_fixture_factory("col_num10", "decimal(10,1)", source_delimiter='`'),
        schema_fixture_factory("col_dec", "decimal(20,1)", source_delimiter='`'),
        schema_fixture_factory("col_numeric_2", "decimal(38,0)", source_delimiter='`'),
        schema_fixture_factory("col_escaped", "double", source_delimiter='`'),
        schema_fixture_factory("`col Escaped2`", "double", source_delimiter='`'),
        schema_fixture_factory('`col escaped3`', "double", source_delimiter='`'),
        schema_fixture_factory('`col"escaped4`', "double", source_delimiter='`'),
        schema_fixture_factory('`col``escaped5`', "double", source_delimiter='`'),
        schema_fixture_factory('`col ``$ EscAped6`', "double", source_delimiter='`'),
    ]
    return src_schema, tgt_schema


def oracle_databricks_schema():
    src_schema = [
        schema_fixture_factory("col_xmltype", "xmltype", source_delimiter='"'),
        schema_fixture_factory("col_char", "char(1)", source_delimiter='"'),
        schema_fixture_factory("col_nchar", "nchar(255)", source_delimiter='"'),
        schema_fixture_factory("col_varchar", "varchar2(255)", source_delimiter='"'),
        schema_fixture_factory("col_varchar2", "varchar2(255)", source_delimiter='"'),
        schema_fixture_factory("col_nvarchar", "nvarchar2(255)", source_delimiter='"'),
        schema_fixture_factory("col_nvarchar2", "nvarchar2(255)", source_delimiter='"'),
        schema_fixture_factory("col_character", "char(255)", source_delimiter='"'),
        schema_fixture_factory("col_clob", "clob", source_delimiter='"'),
        schema_fixture_factory("col_nclob", "nclob", source_delimiter='"'),
        schema_fixture_factory("col_long", "long", source_delimiter='"'),
        schema_fixture_factory("col_number", "number(10,2)", source_delimiter='"'),
        schema_fixture_factory("col_float", "float", source_delimiter='"'),
        schema_fixture_factory("col_binary_float", "binary_float", source_delimiter='"'),
        schema_fixture_factory("col_binary_double", "binary_double", source_delimiter='"'),
        schema_fixture_factory("col_date", "date", source_delimiter='"'),
        schema_fixture_factory("col_timestamp", "timestamp(6)", source_delimiter='"'),
        schema_fixture_factory("col_time_with_tz", "timestamp(6) with time zone", source_delimiter='"'),
        schema_fixture_factory("col_timestamp_with_tz", "timestamp(6) with time zone", source_delimiter='"'),
        schema_fixture_factory("col_timestamp_with_local_tz", "timestamp(6) with local time zone", source_delimiter='"'),
        schema_fixture_factory("col_blob", "blob", source_delimiter='"'),
        schema_fixture_factory("col_rowid", "rowid", source_delimiter='"'),
        schema_fixture_factory("col_urowid", "urowid", source_delimiter='"'),
        schema_fixture_factory("col_anytype", "anytype", source_delimiter='"'),
        schema_fixture_factory("col_anydata", "anydata", source_delimiter='"'),
        schema_fixture_factory("col_anydataset", "anydataset", source_delimiter='"'),
        schema_fixture_factory("col_escaped", "float", source_delimiter='"'),
        schema_fixture_factory("`col Escaped2`", "float", source_delimiter='"'),
        schema_fixture_factory('"col escaped3"', "float", source_delimiter='"'),
        schema_fixture_factory('"col""escaped4"', "float", source_delimiter='"'),
        schema_fixture_factory('"col`escaped5"', "float", source_delimiter='"'),
        schema_fixture_factory('"col `$ EscAped6"', "float", source_delimiter='"'),
        schema_fixture_factory("dummy", "string", source_delimiter='"'),
    ]

    tgt_schema = [
        schema_fixture_factory("col_xmltype", "string", source_delimiter='`'),
        schema_fixture_factory("char", "string", source_delimiter='`'),
        schema_fixture_factory("col_nchar", "string", source_delimiter='`'),
        schema_fixture_factory("col_varchar", "string", source_delimiter='`'),
        schema_fixture_factory("col_varchar2", "string", source_delimiter='`'),
        schema_fixture_factory("col_nvarchar", "string", source_delimiter='`'),
        schema_fixture_factory("col_nvarchar2", "string", source_delimiter='`'),
        schema_fixture_factory("col_character", "string", source_delimiter='`'),
        schema_fixture_factory("col_clob", "string", source_delimiter='`'),
        schema_fixture_factory("col_nclob", "string", source_delimiter='`'),
        schema_fixture_factory("col_long", "string", source_delimiter='`'),
        schema_fixture_factory("col_number", "DECIMAL(10,2)", source_delimiter='`'),
        schema_fixture_factory("col_float", "double", source_delimiter='`'),
        schema_fixture_factory("col_binary_float", "double", source_delimiter='`'),
        schema_fixture_factory("col_binary_double", "double", source_delimiter='`'),
        schema_fixture_factory("col_date", "date", source_delimiter='`'),
        schema_fixture_factory("col_timestamp", "timestamp", source_delimiter='`'),
        schema_fixture_factory("col_time_with_tz", "timestamp", source_delimiter='`'),
        schema_fixture_factory("col_timestamp_with_tz", "timestamp", source_delimiter='`'),
        schema_fixture_factory("col_timestamp_with_local_tz", "timestamp", source_delimiter='`'),
        schema_fixture_factory("col_blob", "binary", source_delimiter='`'),
        schema_fixture_factory("col_rowid", "string", source_delimiter='`'),
        schema_fixture_factory("col_urowid", "string", source_delimiter='`'),
        schema_fixture_factory("col_anytype", "string", source_delimiter='`'),
        schema_fixture_factory("col_anydata", "string", source_delimiter='`'),
        schema_fixture_factory("col_anydataset", "string", source_delimiter='`'),
        schema_fixture_factory("col_escaped", "double", source_delimiter='`'),
        schema_fixture_factory("`col Escaped2`", "double", source_delimiter='`'),
        schema_fixture_factory('`col escaped3`', "double", source_delimiter='`'),
        schema_fixture_factory('`col"escaped4`', "double", source_delimiter='`'),
        schema_fixture_factory('`col``escaped5`', "double", source_delimiter='`'),
        schema_fixture_factory('`col ``$ EscAped6`', "double", source_delimiter='`'),
    ]

    return src_schema, tgt_schema


@pytest.fixture
def schemas():
    return {
        "snowflake_databricks_schema": snowflake_databricks_schema(),
        "databricks_databricks_schema": databricks_databricks_schema(),
        "oracle_databricks_schema": oracle_databricks_schema(),
    }


def test_snowflake_schema_compare(schemas, mock_spark):
    src_schema, tgt_schema = schemas["snowflake_databricks_schema"]
    spark = mock_spark
    table_conf = Table(
        source_name="supplier",
        target_name="supplier",
        drop_columns=["`dummy`"],
        column_mapping=[
            ColumnMapping(source_name="`col_char`", target_name="`char`"),
            ColumnMapping(source_name="`col_array`", target_name="`array_col`"),
        ],
    )

    schema_compare_output = SchemaCompare(spark).compare(
        src_schema,
        tgt_schema,
        get_dialect("snowflake"),
        table_conf,
    )
    df = schema_compare_output.compare_df
    assert not schema_compare_output.is_valid
    assert df.count() == 35
    assert df.filter("is_valid = 'true'").count() == 34
    assert df.filter("is_valid = 'false'").count() == 1


def test_databricks_schema_compare(schemas, mock_spark):
    src_schema, tgt_schema = schemas["databricks_databricks_schema"]
    spark = mock_spark
    table_conf = Table(
        source_name="supplier",
        target_name="supplier",
        select_columns=[
            "`col_boolean`",
            "`col_char`",
            "`col_int`",
            "`col_string`",
            "`col_bigint`",
            "`col_num10`",
            "`col_dec`",
            "`col_numeric_2`",
            "`col_escaped`",
            "`col Escaped2`",
            '`col escaped3`',
            '`col"escaped4`',
            '`col``escaped5`',
            '`col ``$ EscAped6`',
        ],
        column_mapping=[
            ColumnMapping(source_name="`col_char`", target_name="`char`"),
            ColumnMapping(source_name="`col_array`", target_name="`array_col`"),
        ],
    )
    schema_compare_output = SchemaCompare(spark).compare(
        src_schema,
        tgt_schema,
        get_dialect("databricks"),
        table_conf,
    )
    df = schema_compare_output.compare_df

    assert not schema_compare_output.is_valid
    assert df.count() == 14
    assert df.filter("is_valid = 'true'").count() == 13
    assert df.filter("is_valid = 'false'").count() == 1


def test_oracle_schema_compare(schemas, mock_spark):
    src_schema, tgt_schema = schemas["oracle_databricks_schema"]
    spark = mock_spark
    table_conf = Table(
        source_name="supplier",
        target_name="supplier",
        drop_columns=["`dummy`"],
        column_mapping=[
            ColumnMapping(source_name="`col_char`", target_name="`char`"),
            ColumnMapping(source_name="`col_array`", target_name="`array_col`"),
        ],
    )
    schema_compare_output = SchemaCompare(spark).compare(
        src_schema,
        tgt_schema,
        get_dialect("oracle"),
        table_conf,
    )
    df = schema_compare_output.compare_df

    assert schema_compare_output.is_valid
    assert df.count() == 32
    assert df.filter("is_valid = 'true'").count() == 32
    assert df.filter("is_valid = 'false'").count() == 0


def test_schema_compare(mock_spark):
    src_schema = [
        schema_fixture_factory("col1", "int", source_delimiter="`"),
        schema_fixture_factory("col2", "string", source_delimiter="`"),
    ]
    tgt_schema = [
        schema_fixture_factory("col1", "int", source_delimiter="`"),
        schema_fixture_factory("col2", "string", source_delimiter="`"),
    ]
    spark = mock_spark
    table_conf = Table(
        source_name="supplier",
        target_name="supplier",
        drop_columns=["`dummy`"],
        column_mapping=[
            ColumnMapping(source_name="`col_char`", target_name="`char`"),
            ColumnMapping(source_name="`col_array`", target_name="`array_col`"),
        ],
    )

    schema_compare_output = SchemaCompare(spark).compare(
        src_schema,
        tgt_schema,
        get_dialect("databricks"),
        table_conf,
    )
    df = schema_compare_output.compare_df

    assert schema_compare_output.is_valid
    assert df.count() == 2
    assert df.filter("is_valid = 'true'").count() == 2
    assert df.filter("is_valid = 'false'").count() == 0
