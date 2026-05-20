"""Unit tests for GlueTransformer — inline string-based, no fixture files."""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from databricks.labs.lakebridge.transpiler.glue.glue_transformer import (
    GlueTransformer,
    _map_glue_type,
)


def _transform(source: str, catalog: str | None = None, args_style: str = "argparse") -> tuple[str, list[str]]:
    t = GlueTransformer(Path("test_job.py"), catalog=catalog, args_style=args_style)
    return t.transform(textwrap.dedent(source))


# ──────────────────────────────────────────────────────────────────────────────
# Import rewriting
# ──────────────────────────────────────────────────────────────────────────────


def test_awsglue_context_import_replaced():
    code, _ = _transform(
        """\
        from awsglue.context import GlueContext
        x = 1
        """
    )
    assert "from pyspark.sql import SparkSession" in code
    assert "awsglue" not in code


def test_awsglue_transforms_import_removed():
    code, _ = _transform(
        """\
        from awsglue.transforms import *
        x = 1
        """
    )
    assert "awsglue" not in code


def test_awsglue_utils_import_replaced_with_argparse():
    code, _ = _transform(
        """\
        from awsglue.utils import getResolvedOptions
        x = 1
        """
    )
    assert "import argparse" in code
    assert "awsglue" not in code


def test_awsglue_dynamicframe_import_removed():
    code, _ = _transform(
        """\
        from awsglue.dynamicframe import DynamicFrame
        x = 1
        """
    )
    assert "awsglue" not in code


def test_awsglue_job_import_removed():
    code, _ = _transform(
        """\
        from awsglue.job import Job
        x = 1
        """
    )
    assert "awsglue" not in code


def test_non_awsglue_imports_preserved():
    code, _ = _transform(
        """\
        from awsglue.context import GlueContext
        import pandas as pd
        from pyspark.sql import functions as F
        x = 1
        """
    )
    assert "import pandas as pd" in code
    assert "from pyspark.sql import functions as F" in code


def test_new_imports_inserted_after_existing_imports():
    code, _ = _transform(
        """\
        from awsglue.context import GlueContext
        from pyspark.context import SparkContext
        import os

        sc = SparkContext()
        glueContext = GlueContext(sc)
        spark = glueContext.spark_session
        """
    )
    lines = [l for l in code.splitlines() if l.strip()]
    import_lines = [i for i, l in enumerate(lines) if l.startswith(("import ", "from "))]
    non_import_lines = [i for i, l in enumerate(lines) if not l.startswith(("import ", "from "))]
    # All imports should appear before non-import statements
    assert max(import_lines) < min(non_import_lines)


# ──────────────────────────────────────────────────────────────────────────────
# GlueContext setup collapsed
# ──────────────────────────────────────────────────────────────────────────────


def test_glue_context_setup_collapsed():
    code, _ = _transform(
        """\
        from awsglue.context import GlueContext
        from pyspark.context import SparkContext

        sc = SparkContext()
        glueContext = GlueContext(sc)
        spark = glueContext.spark_session
        """
    )
    assert "SparkSession.builder.getOrCreate()" in code
    assert "SparkContext()" not in code
    assert "GlueContext(" not in code
    assert "spark_session" not in code


# ──────────────────────────────────────────────────────────────────────────────
# DynamicFrame reads
# ──────────────────────────────────────────────────────────────────────────────


def test_from_catalog_simple():
    code, warnings = _transform(
        """\
        from awsglue.context import GlueContext
        from pyspark.context import SparkContext

        sc = SparkContext()
        glueContext = GlueContext(sc)
        spark = glueContext.spark_session

        df = glueContext.create_dynamic_frame.from_catalog(database="mydb", table_name="mytable")
        """
    )
    # repr() produces single quotes for string literals
    assert "spark.read.table('mydb.mytable')" in code
    assert not warnings


def test_from_catalog_with_unity_catalog():
    code, warnings = _transform(
        """\
        from awsglue.context import GlueContext
        from pyspark.context import SparkContext

        sc = SparkContext()
        glueContext = GlueContext(sc)
        spark = glueContext.spark_session

        df = glueContext.create_dynamic_frame.from_catalog(database="mydb", table_name="mytable")
        """,
        catalog="my_catalog",
    )
    assert "spark.read.table('my_catalog.mydb.mytable')" in code
    assert not warnings


def test_from_options_s3_read():
    code, _ = _transform(
        """\
        from awsglue.context import GlueContext
        from pyspark.context import SparkContext

        sc = SparkContext()
        glueContext = GlueContext(sc)
        spark = glueContext.spark_session

        df = glueContext.create_dynamic_frame.from_options(
            connection_type="s3",
            connection_options={"path": "s3://bucket/prefix/"},
            format="parquet",
        )
        """
    )
    assert "spark.read.format('parquet').load('s3://bucket/prefix/')" in code


def test_from_options_s3a_path_normalized():
    code, _ = _transform(
        """\
        from awsglue.context import GlueContext
        from pyspark.context import SparkContext

        sc = SparkContext()
        glueContext = GlueContext(sc)
        spark = glueContext.spark_session

        df = glueContext.create_dynamic_frame.from_options(
            connection_type="s3",
            connection_options={"path": "s3a://bucket/prefix/"},
            format="parquet",
        )
        """
    )
    assert "s3a://" not in code
    assert "s3://bucket/prefix/" in code


def test_from_options_s3n_path_normalized():
    code, _ = _transform(
        """\
        from awsglue.context import GlueContext
        from pyspark.context import SparkContext

        sc = SparkContext()
        glueContext = GlueContext(sc)
        spark = glueContext.spark_session

        df = glueContext.create_dynamic_frame.from_options(
            connection_type="s3",
            connection_options={"path": "s3n://bucket/prefix/"},
            format="parquet",
        )
        """
    )
    assert "s3n://" not in code
    assert "s3://bucket/prefix/" in code


# JDBC read
def test_from_options_jdbc_read():
    code, warnings = _transform(
        """\
        from awsglue.context import GlueContext
        from pyspark.context import SparkContext

        sc = SparkContext()
        glueContext = GlueContext(sc)
        spark = glueContext.spark_session

        df = glueContext.create_dynamic_frame.from_options(
            connection_type="jdbc",
            connection_options={
                "url": "jdbc:postgresql://host:5432/db",
                "dbtable": "public.orders",
            },
        )
        """
    )
    assert "spark.read.format('jdbc')" in code
    assert ".option('url', 'jdbc:postgresql://host:5432/db')" in code
    assert ".option('dbtable', 'public.orders')" in code
    assert ".load()" in code
    assert not warnings


def test_from_options_unknown_type_warns():
    _, warnings = _transform(
        """\
        from awsglue.context import GlueContext
        from pyspark.context import SparkContext

        sc = SparkContext()
        glueContext = GlueContext(sc)
        spark = glueContext.spark_session

        df = glueContext.create_dynamic_frame.from_options(
            connection_type="dynamodb",
            connection_options={"tableName": "mytable"},
        )
        """
    )
    assert any("dynamodb" in w for w in warnings)


# ──────────────────────────────────────────────────────────────────────────────
# ApplyMapping
# ──────────────────────────────────────────────────────────────────────────────


def test_apply_mapping_injects_col_import():
    code, _ = _transform(
        """\
        from awsglue.context import GlueContext
        from awsglue.transforms import *
        from pyspark.context import SparkContext

        sc = SparkContext()
        glueContext = GlueContext(sc)
        spark = glueContext.spark_session

        src = glueContext.create_dynamic_frame.from_catalog(database="db", table_name="t")
        out = ApplyMapping.apply(frame=src, mappings=[("col_a", "string", "col_a", "string")])
        """
    )
    assert "from pyspark.sql.functions import col" in code


def test_apply_mapping_same_name_no_rename():
    code, _ = _transform(
        """\
        from awsglue.context import GlueContext
        from awsglue.transforms import *
        from pyspark.context import SparkContext

        sc = SparkContext()
        glueContext = GlueContext(sc)
        spark = glueContext.spark_session

        src = glueContext.create_dynamic_frame.from_catalog(database="db", table_name="t")
        out = ApplyMapping.apply(frame=src, mappings=[("col_a", "string", "col_a", "string")])
        """
    )
    # Same name → no withColumnRenamed, only cast
    assert "withColumnRenamed" not in code
    assert ".withColumn('col_a', col('col_a').cast('string'))" in code


def test_apply_mapping_rename_and_cast():
    code, _ = _transform(
        """\
        from awsglue.context import GlueContext
        from awsglue.transforms import *
        from pyspark.context import SparkContext

        sc = SparkContext()
        glueContext = GlueContext(sc)
        spark = glueContext.spark_session

        src = glueContext.create_dynamic_frame.from_catalog(database="db", table_name="t")
        out = ApplyMapping.apply(frame=src, mappings=[("old_id", "string", "new_id", "long")])
        """
    )
    assert ".withColumnRenamed('old_id', 'new_id')" in code
    assert ".cast('bigint')" in code


def test_apply_mapping_glue_type_conversions():
    code, _ = _transform(
        """\
        from awsglue.context import GlueContext
        from awsglue.transforms import *
        from pyspark.context import SparkContext

        sc = SparkContext()
        glueContext = GlueContext(sc)
        spark = glueContext.spark_session

        src = glueContext.create_dynamic_frame.from_catalog(database="db", table_name="t")
        out = ApplyMapping.apply(frame=src, mappings=[
            ("a", "byte",  "a", "byte"),
            ("b", "short", "b", "short"),
            ("c", "bool",  "c", "bool"),
            ("d", "char",  "d", "char"),
        ])
        """
    )
    assert ".cast('tinyint')" in code   # byte → tinyint
    assert ".cast('smallint')" in code  # short → smallint
    assert ".cast('boolean')" in code   # bool → boolean
    assert ".cast('string')" in code    # char → string


def test_apply_mapping_decimal_preserved():
    code, _ = _transform(
        """\
        from awsglue.context import GlueContext
        from awsglue.transforms import *
        from pyspark.context import SparkContext

        sc = SparkContext()
        glueContext = GlueContext(sc)
        spark = glueContext.spark_session

        src = glueContext.create_dynamic_frame.from_catalog(database="db", table_name="t")
        out = ApplyMapping.apply(frame=src, mappings=[
            ("amount", "decimal(10,2)", "amount", "decimal(10,2)"),
        ])
        """
    )
    assert ".cast('decimal(10,2)')" in code


# ──────────────────────────────────────────────────────────────────────────────
# Writes
# ──────────────────────────────────────────────────────────────────────────────


def test_write_s3_parquet():
    code, _ = _transform(
        """\
        from awsglue.context import GlueContext
        from pyspark.context import SparkContext

        sc = SparkContext()
        glueContext = GlueContext(sc)
        spark = glueContext.spark_session

        output_df = spark.read.table("db.t")

        glueContext.write_dynamic_frame.from_options(
            frame=output_df,
            connection_type="s3",
            connection_options={"path": "s3://bucket/out/"},
            format="parquet",
        )
        """
    )
    assert "output_df.write.format('parquet').save('s3://bucket/out/')" in code


def test_write_partitioned():
    code, _ = _transform(
        """\
        from awsglue.context import GlueContext
        from pyspark.context import SparkContext

        sc = SparkContext()
        glueContext = GlueContext(sc)
        spark = glueContext.spark_session

        out_df = spark.read.table("db.t")

        glueContext.write_dynamic_frame.from_options(
            frame=out_df,
            connection_type="s3",
            connection_options={"path": "s3://bucket/out/", "partitionKeys": ["year", "month"]},
            format="parquet",
        )
        """
    )
    assert ".partitionBy('year', 'month')" in code
    assert ".save('s3://bucket/out/')" in code


def test_write_jdbc():
    code, warnings = _transform(
        """\
        from awsglue.context import GlueContext
        from pyspark.context import SparkContext

        sc = SparkContext()
        glueContext = GlueContext(sc)
        spark = glueContext.spark_session

        result_df = spark.read.table("db.t")

        glueContext.write_dynamic_frame.from_options(
            frame=result_df,
            connection_type="jdbc",
            connection_options={
                "url": "jdbc:postgresql://host:5432/db",
                "dbtable": "public.orders_out",
            },
        )
        """
    )
    assert "result_df.write.format('jdbc')" in code
    assert ".option('url', 'jdbc:postgresql://host:5432/db')" in code
    assert ".option('dbtable', 'public.orders_out')" in code
    assert ".save()" in code
    assert not warnings


# ──────────────────────────────────────────────────────────────────────────────
# Job boilerplate
# ──────────────────────────────────────────────────────────────────────────────


def test_job_boilerplate_removed():
    code, _ = _transform(
        """\
        from awsglue.context import GlueContext
        from awsglue.job import Job
        from pyspark.context import SparkContext

        sc = SparkContext()
        glueContext = GlueContext(sc)
        spark = glueContext.spark_session

        job = Job(glueContext)
        job.init("my-job", {})

        result = 1

        job.commit()
        """
    )
    assert "Job(" not in code
    assert "job.init(" not in code
    assert "job.commit()" not in code
    assert "result = 1" in code


# ──────────────────────────────────────────────────────────────────────────────
# getResolvedOptions → argparse
# ──────────────────────────────────────────────────────────────────────────────


def test_args_static_list():
    code, warnings = _transform(
        """\
        import sys
        from awsglue.utils import getResolvedOptions

        args = getResolvedOptions(sys.argv, ["JOB_NAME", "param1", "param2"])

        val = args["param1"]
        """
    )
    assert "_parser = argparse.ArgumentParser()" in code
    assert '_parser.add_argument("--param1")' in code
    assert '_parser.add_argument("--param2")' in code
    # JOB_NAME should NOT become an argparse argument
    assert "JOB_NAME" not in code
    assert "args = vars(_parser.parse_args())" in code
    assert not warnings


def test_args_dynamic_list_warns():
    _, warnings = _transform(
        """\
        import sys
        from awsglue.utils import getResolvedOptions

        param_list = ["param1", "param2"]
        args = getResolvedOptions(sys.argv, param_list)
        """
    )
    assert any("non-literal" in w for w in warnings)


def test_args_dbutils_style():
    code, warnings = _transform(
        """\
        import sys
        from awsglue.utils import getResolvedOptions

        args = getResolvedOptions(sys.argv, ["JOB_NAME", "source_db", "output_path"])

        source = args["source_db"]
        """,
        args_style="dbutils",
    )
    assert "dbutils.widgets.text('source_db', \"\")" in code
    assert "dbutils.widgets.text('output_path', \"\")" in code
    assert "dbutils.widgets.get" in code
    assert "argparse" not in code
    assert "JOB_NAME" not in code
    assert not warnings


# ──────────────────────────────────────────────────────────────────────────────
# Unsupported transforms
# ──────────────────────────────────────────────────────────────────────────────


def test_unsupported_transform_warns():
    _, warnings = _transform(
        """\
        from awsglue.context import GlueContext
        from awsglue.transforms import *
        from pyspark.context import SparkContext

        sc = SparkContext()
        glueContext = GlueContext(sc)
        spark = glueContext.spark_session

        df = glueContext.create_dynamic_frame.from_catalog(database="db", table_name="t")
        out = ResolveChoice.apply(frame=df, choice="make_cols")
        """
    )
    assert any("ResolveChoice" in w for w in warnings)


# ──────────────────────────────────────────────────────────────────────────────
# Comment & whitespace preservation
# ──────────────────────────────────────────────────────────────────────────────


def test_comments_preserved():
    code, _ = _transform(
        """\
        # This is a top-level comment
        import sys  # inline comment

        # Section comment
        x = 1
        """
    )
    assert "# This is a top-level comment" in code
    assert "# inline comment" in code
    assert "# Section comment" in code


def test_blank_lines_preserved():
    source = textwrap.dedent(
        """\
        import sys

        x = 1

        y = 2
        """
    )
    code, _ = _transform(source)
    assert "\n\n" in code


# ──────────────────────────────────────────────────────────────────────────────
# Error handling
# ──────────────────────────────────────────────────────────────────────────────


def test_syntax_error_raises():
    t = GlueTransformer(Path("bad.py"))
    with pytest.raises(Exception):
        t.transform("def broken(:\n    pass")


# ──────────────────────────────────────────────────────────────────────────────
# _map_glue_type
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "glue_type,expected",
    [
        ("long", "bigint"),
        ("short", "smallint"),
        ("byte", "tinyint"),
        ("bool", "boolean"),
        ("char", "string"),
        ("varchar", "string"),
        ("int", "int"),
        ("integer", "int"),
        ("double", "double"),
        ("float", "float"),
        ("string", "string"),
        ("binary", "binary"),
        ("date", "date"),
        ("timestamp", "timestamp"),
        # decimal with precision/scale preserved as-is
        ("decimal(10,2)", "decimal(10,2)"),
        ("DECIMAL(18, 4)", "decimal(18, 4)"),
        # unknown type passed through
        ("custom_type", "custom_type"),
    ],
)
def test_map_glue_type(glue_type: str, expected: str):
    assert _map_glue_type(glue_type) == expected
