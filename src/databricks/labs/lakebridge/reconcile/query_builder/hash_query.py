import logging

import sqlglot.expressions as exp
from sqlglot import Dialect, parse_one

from databricks.labs.lakebridge.reconcile.connectors.data_source import DataSource
from databricks.labs.lakebridge.reconcile.query_builder.base import QueryBuilder
from databricks.labs.lakebridge.reconcile.query_builder.column_transformer import ColumnTransformer
from databricks.labs.lakebridge.reconcile.query_builder.expression_generator import (
    build_column,
    build_column_no_alias,
    concat,
    get_hash_transform,
    lower,
    transform_expression,
)
from databricks.labs.lakebridge.reconcile.recon_config import Schema, Table

logger = logging.getLogger(__name__)


_HASH_COLUMN_NAME = "hash_value_recon"

# Canonical table-reference placeholder used by ``build_query``. sqlglot renders it per
# dialect: Spark/Databricks keep ``:tbl``; Postgres-family dialects (e.g. Redshift) emit
# the pyformat ``%(tbl)s``. On the normal path the placeholder is left in the returned
# query for the connector's ``read_data`` to fill with the real table; when ``build_query``
# is given a ``from_expression`` it splices that in directly (see below), covering both
# rendered forms so callers never hard-code dialect-specific placeholder syntax.
_TABLE_PLACEHOLDER = ":tbl"
_RENDERED_TABLE_PLACEHOLDERS = (":tbl", "%(tbl)s")


def _hash_transform(
    node: exp.Expression,
    source: Dialect,
    layer: str,
    engine: Dialect,
    override: str | None,
) -> exp.Expression:
    if override is not None:
        return parse_one(override.replace("{}", node.sql(dialect=engine)), read=engine)
    return transform_expression(node, get_hash_transform(source, layer))


class HashQueryBuilder(QueryBuilder):

    def __init__(
        self,
        table_conf: Table,
        schema: list[Schema],
        layer: str,
        source_engine: Dialect,
        data_source: DataSource,
        transformer: ColumnTransformer,
        hash_expression_override: str | None = None,
    ):
        super().__init__(table_conf, schema, layer, source_engine, data_source, transformer)
        self._hash_expression_override = hash_expression_override

    def ordered_hash_columns(self) -> list[str]:
        """Hash-column set in the deterministic order used to build the row hash.

        Set = ``(join ∪ select) − thresholds − drops``. Order = by the unnormalized,
        case-insensitive identifier, so that source and target — which can differ by
        ``column_mapping`` and delimiter style — concatenate the same sequence and
        therefore produce the same hash. This is the single definition of "which
        columns participate, in what order"; both ``build_query`` and the fingerprint
        pre-check consume it.
        """
        _join_columns = self.join_columns if self.join_columns else set()
        hash_cols = sorted((_join_columns | self.select_columns) - self.threshold_columns - self.drop_columns)
        return sorted(hash_cols, key=lambda col: self._unnormalize_identifier(col).lower())

    def build_query(
        self, report_type: str, *, project_all_columns: bool = False, from_expression: str | None = None
    ) -> str:
        """Build the hash query for the configured layer.

        ``project_all_columns`` (keyword-only): when True, the projection includes
        every hashed column (not just join + partition keys). Fingerprint Stage-2
        surgical fetch needs this so the compare layer can populate
        ``mismatch_columns`` without a second round-trip. Source and target sides
        MUST be invoked with the same value or ``capture_mismatch_data_and_columns``
        raises on diverging column sets.

        ``from_expression`` (keyword-only): when given, the returned query reads from
        this table reference / subquery instead of leaving the ``:tbl`` placeholder for
        the connector's ``read_data`` to fill. The fingerprint Stage-2 fetch passes its
        bucket-filter subquery here so the query is complete as built. The value is
        spliced into the rendered SQL as a raw string — deliberately NOT re-parsed
        through sqlglot — so a hand-built dialect-specific subquery is preserved
        byte-for-byte (see ``fingerprint/query_builders/redshift.py``).
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

        # Hash the columns in the canonical order (see ``ordered_hash_columns``) so a
        # column_mapping or delimiter difference between source and target does not change
        # the concatenation sequence and therefore the hash value.
        # Fix for https://github.com/databrickslabs/lakebridge/issues/2195
        hashcols_sorted_as_src_seq = [
            self._build_column_name_source_normalized(col) for col in self.ordered_hash_columns()
        ]

        key_cols_with_transform = [
            rendered.column for rendered in self._transformer.transform_user(cols_with_alias, self.layer)
        ]
        hash_col_with_transform = [self._generate_hash_algorithm(hashcols_sorted_as_src_seq, _HASH_COLUMN_NAME)]

        res = (
            exp.select(*hash_col_with_transform + key_cols_with_transform)
            .from_(_TABLE_PLACEHOLDER)
            .where(self.filter, dialect=self.engine)
            .sql(dialect=self.engine)
        )

        if from_expression is not None:
            # Raw string splice (not a sqlglot re-render) so a hand-built dialect
            # subquery survives byte-for-byte; covers both rendered placeholder forms.
            for placeholder in _RENDERED_TABLE_PLACEHOLDERS:
                res = res.replace(placeholder, from_expression)

        logger.info(f"Hash Query for {self.layer}: {res}")
        return res

    def _generate_hash_algorithm(
        self,
        cols: list[str],
        column_alias: str,
    ) -> exp.Expression:
        cols_no_alias = [build_column_no_alias(this=col) for col in cols]
        cols_with_transform = [rendered.column for rendered in self._transformer.transform(cols_no_alias, self.layer)]
        col_exprs = exp.select(*cols_with_transform).iter_expressions()
        # We now use exp.Dpipe to force the use of CONCAT() function across all dialects to be dialect specific || or + in TSQL
        concat_expr = concat(col_exprs)
        hash_expr = concat_expr.transform(
            _hash_transform, self._source_engine, self.layer, self.engine, self._hash_expression_override
        ).transform(lower, is_expr=True)
        return build_column(hash_expr, alias=column_alias)
