"""Unit tests for GlueEngine and the parametrized fixture-based transpilation."""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from databricks.labs.lakebridge.transpiler.glue.glue_engine import GlueEngine
from databricks.labs.lakebridge.transpiler.transpile_status import ErrorSeverity

_GLUE_FIXTURES = Path(__file__).parent.parent.parent / "resources" / "functional" / "glue"

# Fixtures that require non-default engine options are excluded from the default
# parametrized suite and tested separately below.
_SKIP_IN_DEFAULT_SUITE = frozenset(["test_dbutils_widgets"])


def _load_fixture_pairs(root: Path, skip: frozenset[str] = frozenset()) -> list[tuple[Path, Path]]:
    pairs: list[tuple[Path, Path]] = []
    for input_file in sorted(root.rglob("test_*.py")):
        if input_file.stem.endswith("_expected"):
            continue
        if input_file.stem in skip:
            continue
        expected_file = input_file.with_stem(input_file.stem + "_expected")
        if expected_file.exists():
            pairs.append((input_file, expected_file))
    return pairs


_ALL_FIXTURES = _load_fixture_pairs(_GLUE_FIXTURES, skip=_SKIP_IN_DEFAULT_SUITE)
_FIXTURE_IDS = [f"{f.parent.name}/{f.stem}" for f, _ in _ALL_FIXTURES]


@pytest.fixture
def engine() -> GlueEngine:
    return GlueEngine()


# ──────────────────────────────────────────────────────────────────────────────
# Engine contract tests
# ──────────────────────────────────────────────────────────────────────────────


def test_transpiler_name(engine: GlueEngine):
    assert engine.transpiler_name == "glue"


def test_supported_dialects(engine: GlueEngine):
    assert "glue" in engine.supported_dialects


def test_is_supported_file_py(engine: GlueEngine):
    assert engine.is_supported_file(Path("job.py"))


def test_is_supported_file_py_uppercase(engine: GlueEngine):
    assert engine.is_supported_file(Path("job.PY"))


def test_is_not_supported_file_sql(engine: GlueEngine):
    assert not engine.is_supported_file(Path("query.sql"))


def test_is_not_supported_file_ipynb(engine: GlueEngine):
    assert not engine.is_supported_file(Path("notebook.ipynb"))


def test_transpile_returns_transpile_result(engine: GlueEngine):
    from databricks.labs.lakebridge.config import TranspileResult

    result = asyncio.run(engine.transpile("glue", "databricks", "x = 1\n", Path("x.py")))
    assert isinstance(result, TranspileResult)


def test_transpile_success_count_is_one(engine: GlueEngine):
    result = asyncio.run(engine.transpile("glue", "databricks", "x = 1\n", Path("x.py")))
    assert result.success_count == 1


def test_transpile_syntax_error_returns_zero_success(engine: GlueEngine):
    result = asyncio.run(engine.transpile("glue", "databricks", "def bad(:\n    pass", Path("bad.py")))
    assert result.success_count == 0
    assert result.error_list
    assert result.error_list[0].severity.value == "ERROR"


def test_transpile_warnings_appear_in_error_list(engine: GlueEngine):
    source = """\
from awsglue.context import GlueContext
from awsglue.transforms import *
from pyspark.context import SparkContext

sc = SparkContext()
glueContext = GlueContext(sc)
spark = glueContext.spark_session

df = glueContext.create_dynamic_frame.from_catalog(database="db", table_name="t")
out = ResolveChoice.apply(frame=df, choice="make_cols")
"""
    result = asyncio.run(engine.transpile("glue", "databricks", source, Path("job.py")))
    assert result.success_count == 1
    assert any(e.severity == ErrorSeverity.WARNING for e in result.error_list)


# ──────────────────────────────────────────────────────────────────────────────
# Engine options: catalog and args_style
# ──────────────────────────────────────────────────────────────────────────────


def test_engine_catalog_option_applied():
    """Unity Catalog prefix is prepended when catalog option is configured."""
    from databricks.labs.lakebridge.config import TranspileConfig

    engine = GlueEngine()
    config = TranspileConfig(
        transpiler_config_path="glue",
        source_dialect="glue",
        output_folder="/tmp",
        transpiler_options={"catalog": "my_catalog"},
    )
    asyncio.run(engine.initialize(config))

    source = """\
from awsglue.context import GlueContext
from pyspark.context import SparkContext

sc = SparkContext()
glueContext = GlueContext(sc)
spark = glueContext.spark_session

df = glueContext.create_dynamic_frame.from_catalog(database="mydb", table_name="orders")
"""
    result = asyncio.run(engine.transpile("glue", "databricks", source, Path("job.py")))
    assert "my_catalog.mydb.orders" in result.transpiled_code


def test_engine_dbutils_args_style_applied():
    """dbutils.widgets blocks are generated when args-style=dbutils is configured."""
    from databricks.labs.lakebridge.config import TranspileConfig

    engine = GlueEngine()
    config = TranspileConfig(
        transpiler_config_path="glue",
        source_dialect="glue",
        output_folder="/tmp",
        transpiler_options={"args-style": "dbutils"},
    )
    asyncio.run(engine.initialize(config))

    source = """\
import sys
from awsglue.utils import getResolvedOptions

args = getResolvedOptions(sys.argv, ["JOB_NAME", "source_db"])

val = args["source_db"]
"""
    result = asyncio.run(engine.transpile("glue", "databricks", source, Path("job.py")))
    assert "dbutils.widgets.text" in result.transpiled_code
    assert "argparse" not in result.transpiled_code


def test_engine_invalid_output_adds_warning(monkeypatch):
    """If the transformer produces syntactically invalid Python, a warning is added."""
    from databricks.labs.lakebridge.transpiler.glue import glue_engine as ge_mod

    original_transformer = ge_mod.GlueTransformer

    class _BrokenTransformer:
        def __init__(self, *args, **kwargs):
            pass

        def transform(self, source_code: str) -> tuple[str, list[str]]:
            return "def broken(:\n    pass\n", []

    monkeypatch.setattr(ge_mod, "GlueTransformer", _BrokenTransformer)

    engine = GlueEngine()
    result = asyncio.run(engine.transpile("glue", "databricks", "x = 1\n", Path("x.py")))
    assert any("syntax error" in w.message.lower() for w in result.error_list)


# ──────────────────────────────────────────────────────────────────────────────
# Fixture test: dbutils style (requires custom engine)
# ──────────────────────────────────────────────────────────────────────────────


def test_dbutils_widgets_fixture():
    """Fixture test for args/test_dbutils_widgets.py using args_style=dbutils engine."""
    from databricks.labs.lakebridge.config import TranspileConfig

    engine = GlueEngine()
    config = TranspileConfig(
        transpiler_config_path="glue",
        source_dialect="glue",
        output_folder="/tmp",
        transpiler_options={"args-style": "dbutils"},
    )
    asyncio.run(engine.initialize(config))

    input_file = _GLUE_FIXTURES / "args" / "test_dbutils_widgets.py"
    expected_file = _GLUE_FIXTURES / "args" / "test_dbutils_widgets_expected.py"

    source = input_file.read_text()
    expected = expected_file.read_text()
    result = asyncio.run(engine.transpile("glue", "databricks", source, input_file))
    assert result.transpiled_code.strip() == expected.strip()


# ──────────────────────────────────────────────────────────────────────────────
# Parametrized fixture tests (default engine, argparse mode)
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("input_file,expected_file", _ALL_FIXTURES, ids=_FIXTURE_IDS)
def test_glue_fixture(engine: GlueEngine, input_file: Path, expected_file: Path):
    source = input_file.read_text()
    expected = expected_file.read_text()
    result = asyncio.run(engine.transpile("glue", "databricks", source, input_file))
    assert result.transpiled_code.strip() == expected.strip()
