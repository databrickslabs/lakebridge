from databricks.labs.lakebridge.reconcile.query_builder.count_query import CountQueryBuilder
from databricks.labs.lakebridge.transpiler.sqlglot.dialect_utils import get_dialect


def test_count_query(table_conf_with_opts):
    source_query = CountQueryBuilder(
        table_conf=table_conf_with_opts, layer="source", engine=get_dialect("oracle")
    ).build_query()
    target_query = CountQueryBuilder(
        table_conf=table_conf_with_opts, layer="target", engine=get_dialect("databricks")
    ).build_query()
    assert source_query == "SELECT COUNT(1) AS count FROM :tbl WHERE s_name = 't' AND s_address = 'a'"
    assert target_query == "SELECT COUNT(1) AS count FROM :tbl WHERE s_name = 't' AND s_address_t = 'a'"
