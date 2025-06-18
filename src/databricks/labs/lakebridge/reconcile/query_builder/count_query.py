import logging

from sqlglot import Dialect
from sqlglot import expressions as exp

from databricks.labs.lakebridge.reconcile.query_builder.expression_generator import build_column, build_literal
from databricks.labs.lakebridge.reconcile.recon_config import Table

logger = logging.getLogger(__name__)


class CountQueryBuilder:

    def __init__(
        self,
        table_conf: Table,
        layer: str,
        engine: Dialect,
    ):
        self._table_conf = table_conf
        self._layer = layer
        self._engine = engine

    def build_query(self):
        select_clause = build_column(this=exp.Count(this=build_literal(this="1", is_string=False)), alias="count")
        count_query = (
            exp.select(select_clause)
            .from_(":tbl")
            .where(self._table_conf.get_filter(self._layer))
            .sql(dialect=self._engine)
        )
        logger.info(f"Record Count Query for {self._layer}: {count_query}")
        return count_query
