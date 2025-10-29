from databricks.labs.lakebridge.reconcile.query_builder.base import QueryBuilder
from databricks.labs.lakebridge.reconcile.query_builder.expression_generator import build_column
from databricks.labs.lakebridge.reconcile.recon_config import Schema
from tests.unit.conftest import get_dialect


class QueryBuilderUnderTest(QueryBuilder):
    pass


def test_use_default_transformations_for_bogus_input(table_conf, mock_data_source):
    conf = table_conf()
    engine = get_dialect("databricks")
    schema = [Schema("`col1`", "BOGUS_TYPE", "`col1`", "`col1`")]
    exps = [build_column("`col1`")]
    builder = QueryBuilderUnderTest(conf, schema, "source", engine, mock_data_source)

    result = builder.add_transformations(exps, engine)

    assert result != exps


def test_use_type_transformations(table_conf, mock_data_source):
    conf = table_conf()
    engine = get_dialect("databricks")
    schema = [Schema("`col1`", "ARRAY", "`col1`", "`col1`")]
    exps = [build_column("`col1`")]
    builder = QueryBuilderUnderTest(conf, schema, "source", engine, mock_data_source)

    result = builder.add_transformations(exps, engine)

    assert result != exps
