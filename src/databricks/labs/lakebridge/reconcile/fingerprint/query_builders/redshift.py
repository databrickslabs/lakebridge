"""Redshift SQL generation for fingerprint detection and surgical row fetch.

The fingerprint SQL is hand-built with f-strings rather than composed through the
``sqlglot`` AST that the rest of the reconcile query builders use. This is deliberate:
the detection query is a fixed aggregate shape (dual-slice MD5 -> power sums -> GROUP BY
sub-bucket) whose exact byte layout must stay bit-identical to the Spark target
serializer for the row hashes to line up, and round-tripping it through sqlglot's
Redshift generator risks silent re-formatting (function-name casing, cast spelling)
that would break that byte-for-byte contract. Per-column serialization still delegates
to the shared ``serialize_column_for_hash`` transform map so type handling cannot drift.
"""

import logging

from databricks.labs.lakebridge.reconcile.connectors.dialect_utils import DialectUtils
from databricks.labs.lakebridge.reconcile.fingerprint.constants import (
    SEPARATOR_REDSHIFT_SQL,
    build_fingerprint_where_clause,
    quote_identifier,
)
from databricks.labs.lakebridge.reconcile.fingerprint.query_builders.base import FingerprintQueryBuilder
from databricks.labs.lakebridge.reconcile.query_builder.expression_generator import serialize_column_for_hash
from databricks.labs.lakebridge.reconcile.recon_config import Schema
from databricks.labs.lakebridge.transpiler.sqlglot.dialect_utils import get_dialect

logger = logging.getLogger(__name__)


_REDSHIFT_IDENTIFIER_QUOTE = '"'


def quote_redshift_identifier(bare: str) -> str:
    """Wrap ``bare`` in Redshift's ``"..."`` identifier delimiters (see
    ``constants.quote_identifier`` for the escaping + case-preservation rationale)."""
    return quote_identifier(bare, _REDSHIFT_IDENTIFIER_QUOTE)


class RedshiftFingerprintQueryBuilder(FingerprintQueryBuilder):
    """Generate Redshift SQL for fingerprint detection and surgical row fetch."""

    def serialize_column(self, col_name: str, col_type: str) -> str:
        # Delegate per-column serialization to the shared row-hash transform map so the
        # Redshift source byte stream is identical to the row-hash compare path (and the
        # Databricks target) by construction. ``quote_redshift_identifier`` reproduces
        # the source-normalized reference the row-hash path feeds ``build_column_no_alias``.
        bare = DialectUtils.unnormalize_identifier(col_name)
        quoted = quote_redshift_identifier(bare)
        return serialize_column_for_hash(quoted, col_type, get_dialect("redshift"))

    def build_detection_sql(
        self,
        schema: str,
        table: str,
        columns: list[Schema],
        column_mapping: dict[str, str] | None,
        sub_bucket_count: int,
        bucket_count: int,
    ) -> str:
        # ``column_mapping`` is unused on the source side: Redshift reads its own physical
        # names. The ABC carries it for symmetry with dialects whose source-side SQL
        # might need to project differently from the target.
        rh1_expr, rh2_expr, sb_expr = self._md5_hash_exprs(columns, sub_bucket_count)
        bucket_expr = f"ABS(MOD({rh1_expr}, {bucket_count}))"

        # rh*rh exceeds BIGINT range. Cast operands to DECIMAL(19,0) so the multiply lands
        # in DECIMAL(38,0); SUM lifts linear aggregates directly to DECIMAL(38,0).
        rh1_dec19 = f"CAST({rh1_expr} AS DECIMAL(19,0))"
        rh1_dec38 = f"CAST({rh1_expr} AS DECIMAL(38,0))"
        rh2_dec19 = f"CAST({rh2_expr} AS DECIMAL(19,0))"
        rh2_dec38 = f"CAST({rh2_expr} AS DECIMAL(38,0))"

        # Route schema / table through the same identifier-quoting helper used
        # for column names so an exotic name like ``my-schema`` or one carrying
        # a stray ``"`` cannot malform the SQL. Today these are
        # connector-validated and safe, but the cost of being defensive here is
        # one function call.
        from_table = f"{quote_redshift_identifier(schema)}.{quote_redshift_identifier(table)}"
        return (
            f"SELECT {sb_expr} AS sub_bucket_id, "
            f"{bucket_expr} AS bucket_id, "
            f"COUNT(*) AS cnt, "
            f"SUM({rh1_dec38}) AS p1, "
            f"SUM({rh1_dec19} * {rh1_dec19}) AS p2, "
            f"SUM({rh2_dec38}) AS p1_rh2, "
            f"SUM({rh2_dec19} * {rh2_dec19}) AS p2_rh2 "
            f"FROM {from_table} "
            f"GROUP BY sub_bucket_id, bucket_id"
        )

    def build_source_filter_subquery(
        self,
        schema: str,
        table: str,
        columns: list[Schema],
        sub_bucket_count: int,
        solved_hashes: dict[int, list[int]],
        unsolved_sb_ids: list[int],
    ) -> str:
        rh1_expr, _, sb_expr = self._md5_hash_exprs(columns, sub_bucket_count)
        where_clause = build_fingerprint_where_clause(sb_expr, rh1_expr, solved_hashes, unsolved_sb_ids)
        from_table = f"{quote_redshift_identifier(schema)}.{quote_redshift_identifier(table)}"
        return f"(SELECT * FROM {from_table} WHERE {where_clause}) _fp_filtered"

    def build_concat_expression(self, columns: list[Schema]) -> str:
        """Concat over source physical column names for MD5."""
        parts = [self.serialize_column(c.column_name, c.data_type) for c in columns]
        return f" || {SEPARATOR_REDSHIFT_SQL} || ".join(parts)

    def _md5_hash_exprs(self, columns: list[Schema], sub_bucket_count: int) -> tuple[str, str, str]:
        """Return the (rh1, rh2, sb_expr) MD5-derived SQL fragments shared by detection + filter SQL."""
        concat_expr = self.build_concat_expression(columns)
        rh1_expr = f"STRTOL(SUBSTRING(MD5({concat_expr}), 1, 8), 16)"
        rh2_expr = f"STRTOL(SUBSTRING(MD5({concat_expr}), 9, 8), 16)"
        sb_expr = f"ABS(MOD({rh1_expr}, {sub_bucket_count}))"
        return rh1_expr, rh2_expr, sb_expr
