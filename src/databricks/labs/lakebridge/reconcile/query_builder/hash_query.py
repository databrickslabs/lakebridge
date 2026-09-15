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

    def _hash_column_set(self) -> set[str]:
        """The set of columns that participate in the row hash: ``(join ∪ select) − thresholds − drops``.

        Single source of truth for "which columns participate", consumed by both
        ``ordered_hash_columns`` (which orders it for the hash sequence) and
        ``build_query`` (which needs it for the projection). Keeping the set algebra in
        one place stops the two from silently diverging if an exclusion set is ever added.
        """
        _join_columns = self.join_columns if self.join_columns else set()
        return (_join_columns | self.select_columns) - self.threshold_columns - self.drop_columns

    def _source_aligned_sort_key(self, col: str) -> str:
        """Ordering key that sorts a column by its SOURCE-side identifier.

        Both layers must order columns identically for the row hash to line up and for the
        ``project_all_columns`` projection to expose the same column list to
        ``capture_mismatch_data_and_columns``. Sorting by the *layer-local* name (the previous
        behaviour) only aligns when a ``column_mapping`` preserves alphabetical order (e.g. a
        shared suffix). A mapping that *permutes* the order — e.g. source ``alpha`` → target
        ``zeta`` and source ``beta`` → target ``apple`` — desynced the two sides: the row
        hashes diverged (false mismatch on every row) and, on the Stage-2 path, the projection
        raised ``ColumnMismatchException`` in ``capture_mismatch_data_and_columns``. Mapping
        each column back to its source name first (identity on the source layer) makes the
        order source-canonical on both sides. This is the same source identifier
        ``_build_column_with_alias`` uses for the output alias, so the projected column *names*
        and their *order* agree by construction.
        """
        return self._unnormalize_identifier(self.table_conf.get_layer_tgt_to_src_col_mapping(col, self.layer)).lower()

    def ordered_hash_columns(self) -> list[str]:
        """Hash-column set in the deterministic order used to build the row hash.

        Set = ``(join ∪ select) − thresholds − drops`` (see ``_hash_column_set``). Order =
        by the source-side identifier (see ``_source_aligned_sort_key``), so that source and
        target — which can differ by ``column_mapping`` and delimiter style — concatenate the
        same sequence and therefore produce the same hash. This is the single definition of
        "which columns participate, in what order"; both ``build_query`` and the fingerprint
        pre-check consume it.
        """
        return sorted(self._hash_column_set(), key=self._source_aligned_sort_key)

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
        # Every column list is ordered by the source-side identifier (see
        # ``_source_aligned_sort_key`` / ``ordered_hash_columns``) so the source and target
        # layers emit the hash sequence AND the projection in the same order under any
        # ``column_mapping``. ``hash_cols`` is that canonical order over the full hash set.
        hash_cols = self.ordered_hash_columns()

        key_cols = (
            hash_cols
            if report_type == "row"
            else sorted(_join_columns | self.partition_column, key=self._source_aligned_sort_key)
        )
        if project_all_columns and report_type != "row":
            # Widen the SELECT to every hashed column while keeping the same source-aligned
            # order, so ``capture_mismatch_data_and_columns`` sees identical column lists on
            # both sides. ``hash_cols`` is already the sorted superset of the join/partition keys.
            key_cols = sorted(set(key_cols) | set(hash_cols), key=self._source_aligned_sort_key)

        cols_with_alias = [self._build_column_with_alias(col) for col in key_cols]

        # Hash the columns in the same source-aligned order (see ``ordered_hash_columns``) so a
        # column_mapping or delimiter difference between source and target does not change the
        # concatenation sequence and therefore the hash value.
        # Fix for https://github.com/databrickslabs/lakebridge/issues/2195
        hashcols_sorted_as_src_seq = [self._build_column_name_source_normalized(col) for col in hash_cols]

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
            # Splice the caller's FROM source in as a raw string, NOT via a sqlglot re-render.
            # This is the same mechanism every connector's ``read_data`` uses to fill the table
            # placeholder (``query.replace(":tbl", schema.table)``), and it preserves a hand-built
            # dialect subquery byte-for-byte. Re-rendering through sqlglot would rewrite the
            # subquery -- e.g. it turns Redshift ``MOD(x, n)`` into ``x % n`` and
            # ``SUBSTRING(m, 1, 8)`` into ``SUBSTRING(m FROM 1 FOR 8)`` -- which mutates the
            # hand-crafted MD5 bucket arithmetic the Stage-2 filter depends on.
            # Anchor on the rendered ``FROM <placeholder>`` and replace a single occurrence: a
            # bare ``.replace(placeholder, ...)`` would rewrite every occurrence, so a stray
            # ``:tbl`` / ``%(tbl)s`` in a filter literal or identifier elsewhere in the SQL could
            # be corrupted. Exactly one placeholder form is present (dialect-dependent); fail loud
            # if the query shape ever changes.
            for placeholder in _RENDERED_TABLE_PLACEHOLDERS:
                anchored = f"FROM {placeholder}"
                if anchored in res:
                    res = res.replace(anchored, f"FROM {from_expression}", 1)
                    break
            else:
                raise ValueError(f"Expected a table placeholder to splice into, found none in: {res}")

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
