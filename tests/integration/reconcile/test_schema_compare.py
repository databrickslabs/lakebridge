import pytest

from databricks.labs.lakebridge.transpiler.sqlglot.dialect_utils import get_dialect
from databricks.labs.lakebridge.reconcile.recon_config import ColumnMapping, Schema, Table
from databricks.labs.lakebridge.reconcile.schema_compare import SchemaCompare


def snowflake_databricks_schema():
    src_schema = [
        Schema("col_boolean", "boolean"),
        Schema("col_char", "varchar(1)"),
        Schema("col_varchar", "varchar(16777216)"),
        Schema("col_string", "varchar(16777216)"),
        Schema("col_text", "varchar(16777216)"),
        Schema("col_binary", "binary(8388608)"),
        Schema("col_varbinary", "binary(8388608)"),
        Schema("col_int", "number(38,0)"),
        Schema("col_bigint", "number(38,0)"),
        Schema("col_smallint", "number(38,0)"),
        Schema("col_float", "float"),
        Schema("col_float4", "float"),
        Schema("col_double", "float"),
        Schema("col_real", "float"),
        Schema("col_date", "date"),
        Schema("col_time", "time(9)"),
        Schema("col_timestamp", "timestamp_ntz(9)"),
        Schema("col_timestamp_ltz", "timestamp_ltz(9)"),
        Schema("col_timestamp_ntz", "timestamp_ntz(9)"),
        Schema("col_timestamp_tz", "timestamp_tz(9)"),
        Schema("col_variant", "variant"),
        Schema("col_object", "object"),
        Schema("col_array", "array"),
        Schema("col_geography", "geography"),
        Schema("col_num10", "number(10,1)"),
        Schema("col_dec", "number(20,2)"),
        Schema("col_numeric_2", "numeric(38,0)"),
        Schema("dummy", "string"),
    ]
    tgt_schema = [
        Schema("col_boolean", "boolean"),
        Schema("char", "string"),
        Schema("col_varchar", "string"),
        Schema("col_string", "string"),
        Schema("col_text", "string"),
        Schema("col_binary", "binary"),
        Schema("col_varbinary", "binary"),
        Schema("col_int", "decimal(38,0)"),
        Schema("col_bigint", "decimal(38,0)"),
        Schema("col_smallint", "decimal(38,0)"),
        Schema("col_float", "double"),
        Schema("col_float4", "double"),
        Schema("col_double", "double"),
        Schema("col_real", "double"),
        Schema("col_date", "date"),
        Schema("col_time", "timestamp"),
        Schema("col_timestamp", "timestamp_ntz"),
        Schema("col_timestamp_ltz", "timestamp"),
        Schema("col_timestamp_ntz", "timestamp_ntz"),
        Schema("col_timestamp_tz", "timestamp"),
        Schema("col_variant", "variant"),
        Schema("col_object", "string"),
        Schema("array_col", "array<string>"),
        Schema("col_geography", "string"),
        Schema("col_num10", "decimal(10,1)"),
        Schema("col_dec", "decimal(20,1)"),
        Schema("col_numeric_2", "decimal(38,0)"),
    ]
    return src_schema, tgt_schema


def databricks_databricks_schema():
    src_schema = [
        Schema("col_boolean", "boolean"),
        Schema("col_char", "string"),
        Schema("col_int", "int"),
        Schema("col_string", "string"),
        Schema("col_bigint", "int"),
        Schema("col_num10", "decimal(10,1)"),
        Schema("col_dec", "decimal(20,2)"),
        Schema("col_numeric_2", "decimal(38,0)"),
        Schema("dummy", "string"),
    ]
    tgt_schema = [
        Schema("col_boolean", "boolean"),
        Schema("char", "string"),
        Schema("col_int", "int"),
        Schema("col_string", "string"),
        Schema("col_bigint", "int"),
        Schema("col_num10", "decimal(10,1)"),
        Schema("col_dec", "decimal(20,1)"),
        Schema("col_numeric_2", "decimal(38,0)"),
    ]
    return src_schema, tgt_schema


def oracle_databricks_schema():
    src_schema = [
        Schema("col_xmltype", "xmltype"),
        Schema("col_char", "char(1)"),
        Schema("col_nchar", "nchar(255)"),
        Schema("col_varchar", "varchar2(255)"),
        Schema("col_varchar2", "varchar2(255)"),
        Schema("col_nvarchar", "nvarchar2(255)"),
        Schema("col_nvarchar2", "nvarchar2(255)"),
        Schema("col_character", "char(255)"),
        Schema("col_clob", "clob"),
        Schema("col_nclob", "nclob"),
        Schema("col_long", "long"),
        Schema("col_number", "number(10,2)"),
        Schema("col_float", "float"),
        Schema("col_binary_float", "binary_float"),
        Schema("col_binary_double", "binary_double"),
        Schema("col_date", "date"),
        Schema("col_timestamp", "timestamp(6)"),
        Schema("col_time_with_tz", "timestamp(6) with time zone"),
        Schema("col_timestamp_with_tz", "timestamp(6) with time zone"),
        Schema("col_timestamp_with_local_tz", "timestamp(6) with local time zone"),
        Schema("col_blob", "blob"),
        Schema("col_rowid", "rowid"),
        Schema("col_urowid", "urowid"),
        Schema("col_anytype", "anytype"),
        Schema("col_anydata", "anydata"),
        Schema("col_anydataset", "anydataset"),
        Schema("dummy", "string"),
    ]

    tgt_schema = [
        Schema("col_xmltype", "string"),
        Schema("char", "string"),
        Schema("col_nchar", "string"),
        Schema("col_varchar", "string"),
        Schema("col_varchar2", "string"),
        Schema("col_nvarchar", "string"),
        Schema("col_nvarchar2", "string"),
        Schema("col_character", "string"),
        Schema("col_clob", "string"),
        Schema("col_nclob", "string"),
        Schema("col_long", "string"),
        Schema("col_number", "DECIMAL(10,2)"),
        Schema("col_float", "double"),
        Schema("col_binary_float", "double"),
        Schema("col_binary_double", "double"),
        Schema("col_date", "date"),
        Schema("col_timestamp", "timestamp"),
        Schema("col_time_with_tz", "timestamp"),
        Schema("col_timestamp_with_tz", "timestamp"),
        Schema("col_timestamp_with_local_tz", "timestamp"),
        Schema("col_blob", "binary"),
        Schema("col_rowid", "string"),
        Schema("col_urowid", "string"),
        Schema("col_anytype", "string"),
        Schema("col_anydata", "string"),
        Schema("col_anydataset", "string"),
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
        drop_columns=["dummy"],
        column_mapping=[
            ColumnMapping(source_name="col_char", target_name="char"),
            ColumnMapping(source_name="col_array", target_name="array_col"),
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
    assert df.count() == 27
    assert df.filter("is_valid = 'true'").count() == 25
    assert df.filter("is_valid = 'false'").count() == 2


def test_databricks_schema_compare(schemas, mock_spark):
    src_schema, tgt_schema = schemas["databricks_databricks_schema"]
    spark = mock_spark
    table_conf = Table(
        source_name="supplier",
        target_name="supplier",
        select_columns=[
            "col_boolean",
            "col_char",
            "col_int",
            "col_string",
            "col_bigint",
            "col_num10",
            "col_dec",
            "col_numeric_2",
        ],
        column_mapping=[
            ColumnMapping(source_name="col_char", target_name="char"),
            ColumnMapping(source_name="col_array", target_name="array_col"),
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
    assert df.count() == 8
    assert df.filter("is_valid = 'true'").count() == 7
    assert df.filter("is_valid = 'false'").count() == 1


def test_oracle_schema_compare(schemas, mock_spark):
    src_schema, tgt_schema = schemas["oracle_databricks_schema"]
    spark = mock_spark
    table_conf = Table(
        source_name="supplier",
        target_name="supplier",
        drop_columns=["dummy"],
        column_mapping=[
            ColumnMapping(source_name="col_char", target_name="char"),
            ColumnMapping(source_name="col_array", target_name="array_col"),
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
    assert df.count() == 26
    assert df.filter("is_valid = 'true'").count() == 26
    assert df.filter("is_valid = 'false'").count() == 0


def test_schema_compare(mock_spark):
    src_schema = [
        Schema("col1", "int"),
        Schema("col2", "string"),
    ]
    tgt_schema = [
        Schema("col1", "int"),
        Schema("col2", "string"),
    ]
    spark = mock_spark
    table_conf = Table(
        source_name="supplier",
        target_name="supplier",
        drop_columns=["dummy"],
        column_mapping=[
            ColumnMapping(source_name="col_char", target_name="char"),
            ColumnMapping(source_name="col_array", target_name="array_col"),
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
