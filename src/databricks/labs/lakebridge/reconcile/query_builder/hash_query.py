import logging

from functools import reduce
import sqlglot.expressions as exp
from sqlglot import Dialect

from databricks.labs.lakebridge.reconcile.query_builder.base import QueryBuilder
from databricks.labs.lakebridge.reconcile.query_builder.expression_generator import (
    build_column,
    concat,
    get_hash_transform,
    lower,
    transform_expression,
    build_column_no_alias,
)

logger = logging.getLogger(__name__)


def _hash_transform(
    node: exp.Expression,
    source: Dialect,
    layer: str,
):
    transform = get_hash_transform(source, layer)
    return transform_expression(node, transform)


_HASH_COLUMN_NAME = "hash_value_recon"


class HashQueryBuilder(QueryBuilder):

    def build_query(self, report_type: str) -> str:

        if report_type != 'row':
            self._validate(self.join_columns, f"Join Columns are compulsory for {report_type} type")

        _join_columns = self.join_columns if self.join_columns else set()
        hash_cols = sorted((_join_columns | self.select_columns) - self.threshold_columns - self.drop_columns)

        key_cols = hash_cols if report_type == "row" else sorted(_join_columns | self.partition_column)

        cols_with_alias = [self._build_column_with_alias(col) for col in key_cols]

        # in case if we have column mapping, we need to sort the target columns in the order of source columns to get
        # same hash value
        hash_cols_with_alias = [
            {"this": self._build_column_name_source_normalized(col), "alias": self._build_alias_source_normalized(col)}
            for col in hash_cols
        ]
        sorted_hash_cols_with_alias = sorted(hash_cols_with_alias, key=lambda column: column["alias"])
        hashcols_sorted_as_src_seq = [column["this"] for column in sorted_hash_cols_with_alias]

        key_cols_with_transform = (
            self._apply_user_transformation(cols_with_alias) if self.user_transformations else cols_with_alias
        )
        hash_col_with_transform = [self._generate_hash_algorithm(hashcols_sorted_as_src_seq, _HASH_COLUMN_NAME)]

        res = (
            exp.select(*hash_col_with_transform + key_cols_with_transform)
            .from_(":tbl")
            .where(self.filter, dialect=self.engine)
            .sql(dialect=self.engine)
        )

        logger.info(f"Hash Query for {self.layer}: {res}")
        return res

    def _generate_hash_algorithm(
        self,
        cols: list[str],
        column_alias: str,
    ) -> exp.Expression:
        cols_no_alias = [build_column_no_alias(this=col) for col in cols]
        cols_with_transform = self.add_transformations(cols_no_alias, self.engine)
        col_exprs = exp.select(*cols_with_transform).iter_expressions()
        concat_expr = concat(list(col_exprs))

        if self.engine == "oracle":
            concat_expr = reduce(lambda x, y: exp.DPipe(this=x, expression=y), concat_expr.expressions)

        hash_expr = concat_expr.transform(_hash_transform, self.engine, self.layer).transform(lower, is_expr=True)

        return build_column(hash_expr, alias=column_alias)
