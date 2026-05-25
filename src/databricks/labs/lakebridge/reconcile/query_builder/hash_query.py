import logging

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

    def build_query(self, report_type: str, *, project_all_columns: bool = False) -> str:
        """Build the hash query for the configured layer.

        ``project_all_columns`` (keyword-only): when True, the projection includes
        every hashed column (not just join + partition keys). Fingerprint Stage-2
        surgical fetch needs this so the compare layer can populate
        ``mismatch_columns`` without a second round-trip. Source and target sides
        MUST be invoked with the same value or ``capture_mismatch_data_and_columns``
        raises on diverging column sets.
        """

        if report_type != 'row':
            self._validate(self.join_columns, f"Join Columns are compulsory for {report_type} type")

        _join_columns = self.join_columns if self.join_columns else set()
        hash_cols = sorted((_join_columns | self.select_columns) - self.threshold_columns - self.drop_columns)

        key_cols = hash_cols if report_type == "row" else sorted(_join_columns | self.partition_column)
        if project_all_columns and report_type != "row":
            # Union with hash_cols (already a sorted superset of join columns)
            # so we can keep the deterministic projection order while still
            # widening the SELECT list to every hashed column.
            key_cols = sorted(set(key_cols) | set(hash_cols))

        cols_with_alias = [self._build_column_with_alias(col) for col in key_cols]

        # in case if we have column mapping, we need to sort the target columns in the order of source columns to get
        # same hash value. We sort by the unnormalized column name (without delimiters) to ensure consistent
        # ordering across all dialects, regardless of their delimiter characters ([], "", ``, etc.)
        # Fix for https://github.com/databrickslabs/lakebridge/issues/2195
        hash_cols_with_alias = [
            {
                "this": self._build_column_name_source_normalized(col),
                "alias": self._build_alias_source_normalized(col),
                "sort_key": self._unnormalize_identifier(col),
            }
            for col in hash_cols
        ]
        # Sort by unnormalized column name (case-insensitive) to ensure deterministic ordering across all dialects
        sorted_hash_cols_with_alias = sorted(hash_cols_with_alias, key=lambda column: column["sort_key"].lower())
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
        # We now use exp.Dpipe to force the use of CONCAT() function across all dialects to be dialect specific || or + in TSQL
        concat_expr = concat(col_exprs)

        hash_expr = concat_expr.transform(_hash_transform, self._source_engine, self.layer).transform(
            lower, is_expr=True
        )

        return build_column(hash_expr, alias=column_alias)
