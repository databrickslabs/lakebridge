from databricks.labs.lakebridge.reconcile.query_builder.aggregate_query import AggregateQueryBuilder
from databricks.labs.lakebridge.reconcile.recon_config import Aggregate, Table
from databricks.labs.lakebridge.transpiler.sqlglot.dialect_utils import get_dialect


def _build_table(aggregates: list[Aggregate]) -> Table:
    return Table(source_name="supplier", target_name="target_supplier", aggregates=aggregates)


def test_count_star_emits_unquoted_star(fake_databricks_datasource, normalize_config_service):
    """Aggregate(agg_columns=["*"], type="count") must produce COUNT(*), not COUNT(`*`)."""
    table_conf = _build_table([Aggregate(agg_columns=["*"], type="count")])
    normalized = normalize_config_service.normalize_recon_table_config(table_conf)

    rules = AggregateQueryBuilder(
        normalized, [], "source", get_dialect("databricks"), fake_databricks_datasource
    ).build_queries()

    assert len(rules) == 1
    sql = rules[0].query
    assert "count(*)" in sql.lower()
    # Regression: the column-name normalizer must not emit COUNT(`*`)
    assert "count(`*`)" not in sql.lower()
    # Alias of the aggregate column survives normalization
    assert "source_count_*" in sql.lower()


def test_count_star_normalized_input_emits_unquoted_star(fake_databricks_datasource, normalize_config_service):
    """The same fix must hold when agg_columns is already in ansi-normalized form (`*`)."""
    table_conf = _build_table([Aggregate(agg_columns=["`*`"], type="count")])
    normalized = normalize_config_service.normalize_recon_table_config(table_conf)

    rules = AggregateQueryBuilder(
        normalized, [], "target", get_dialect("databricks"), fake_databricks_datasource
    ).build_queries()

    assert len(rules) == 1
    sql = rules[0].query
    assert "count(*)" in sql.lower()
    assert "count(`*`)" not in sql.lower()


def test_count_star_alongside_named_column(fake_databricks_datasource, normalize_config_service):
    """COUNT(*) and COUNT(<col>) must coexist in a single aggregate query."""
    table_conf = _build_table([Aggregate(agg_columns=["*", "s_acctbal"], type="count")])
    normalized = normalize_config_service.normalize_recon_table_config(table_conf)

    rules = AggregateQueryBuilder(
        normalized, [], "source", get_dialect("databricks"), fake_databricks_datasource
    ).build_queries()

    sql = rules[0].query.lower()
    assert "count(*)" in sql
    assert "count(`s_acctbal`)" in sql
    # The * branch must not pollute the named-column branch with backticks around *
    assert "count(`*`)" not in sql


def test_star_with_non_count_aggregate_is_unchanged(fake_databricks_datasource, normalize_config_service):
    """The fast-path must only apply to type='count'. Other aggregates keep the existing behavior."""
    table_conf = _build_table([Aggregate(agg_columns=["*"], type="sum")])
    normalized = normalize_config_service.normalize_recon_table_config(table_conf)

    rules = AggregateQueryBuilder(
        normalized, [], "source", get_dialect("databricks"), fake_databricks_datasource
    ).build_queries()

    sql = rules[0].query.lower()
    # Existing path quotes the identifier; we must not silently turn this into sum(*)
    assert "sum(*)" not in sql
    assert "sum(`*`)" in sql
