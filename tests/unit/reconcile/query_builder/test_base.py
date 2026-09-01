from databricks.labs.lakebridge.reconcile.query_builder.expression_generator import build_column
from databricks.labs.lakebridge.reconcile.recon_config import Schema
from tests.conftest import make_column_transformer
from tests.unit.conftest import get_dialect


def test_use_default_transformations_for_bogus_input(mock_data_source):
    engine = get_dialect("databricks")
    schema = [Schema("`col1`", "BOGUS_TYPE", "`col1`")]
    exps = [build_column("`col1`")]
    transformer = make_column_transformer(schema, engine, mock_data_source)

    result = [r.column for r in transformer.transform(exps, "source")]

    assert result != exps


def test_use_type_transformations(mock_data_source):
    engine = get_dialect("databricks")
    schema = [Schema("`col1`", "ARRAY", "`col1`")]
    exps = [build_column("`col1`")]
    transformer = make_column_transformer(schema, engine, mock_data_source)

    result = [r.column for r in transformer.transform(exps, "source")]

    assert result != exps
