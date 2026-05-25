import logging

from databricks.labs.lakebridge.reconcile.connectors.dialect_utils import DialectUtils
from databricks.labs.lakebridge.reconcile.fingerprint.constants import (
    NULL_SENTINEL,
    SEPARATOR_REDSHIFT_SQL,
    build_fingerprint_where_clause,
)
from databricks.labs.lakebridge.reconcile.fingerprint.query_builders.base import FingerprintQueryBuilder
from databricks.labs.lakebridge.reconcile.recon_config import Schema

logger = logging.getLogger(__name__)


_REDSHIFT_IDENTIFIER_QUOTE = '"'


def _quote_redshift_identifier(bare: str) -> str:
    """Wrap ``bare`` in Redshift's ``"..."`` identifier delimiters, doubling any embedded
    ``"`` per the SQL standard. Defense-in-depth: today's values arrive from
    ``information_schema.columns`` and are trusted, but the persistence layer scrubs
    embedded quotes for the same reason — keep the two boundaries consistent.
    """
    escaped = bare.replace(_REDSHIFT_IDENTIFIER_QUOTE, _REDSHIFT_IDENTIFIER_QUOTE * 2)
    return f"{_REDSHIFT_IDENTIFIER_QUOTE}{escaped}{_REDSHIFT_IDENTIFIER_QUOTE}"


class RedshiftFingerprintQueryBuilder(FingerprintQueryBuilder):
    """Generate Redshift SQL for fingerprint detection and surgical row fetch."""

    def serialize_column(self, col_name: str, col_type: str) -> str:
        # ANSI-delimited identifiers must be re-quoted with double quotes for Redshift.
        bare = DialectUtils.unnormalize_identifier(col_name)
        quoted = _quote_redshift_identifier(bare)

        col_type_lower = (col_type or "").strip().lower()
        if col_type_lower == "boolean":
            # Redshift rejects every form of CAST(boolean AS VARCHAR/TEXT); CASE WHEN
            # produces 'true'/'false' to match Spark's cast(bool AS string).
            cast_expr = f"CASE WHEN {quoted} THEN 'true' WHEN NOT {quoted} THEN 'false' ELSE NULL END"
        elif col_type_lower in {"timestamptz", "timestamp with time zone"}:
            # Parity with the row-hash compare path: ``TO_CHAR`` with a fixed
            # ``YYYY-MM-DD HH24:MI:SS.US`` format pins microsecond width so the
            # byte stream matches the Spark target's ``DATE_FORMAT(_,
            # 'yyyy-MM-dd HH:mm:ss.SSSSSS')``. Default ``CAST(_ AS VARCHAR)``
            # would emit variable-width fractional seconds and silently disagree
            # with row-hash on the same row, dropping it from Stage-2.
            cast_expr = f"TO_CHAR({quoted} AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS.US')"
        elif col_type_lower in {"timestamp", "timestamp without time zone"}:
            cast_expr = f"TO_CHAR({quoted}, 'YYYY-MM-DD HH24:MI:SS.US')"
        elif col_type_lower == "date":
            cast_expr = f"TO_CHAR({quoted}, 'YYYY-MM-DD')"
        else:
            # ``CAST(_ AS VARCHAR)`` in Redshift defaults to ``VARCHAR(256)`` and
            # silently truncates anything longer; the Spark target keeps the
            # full string. That asymmetry would surface a Stage-1 mismatch on
            # otherwise-equal long-text rows. ``VARCHAR(65535)`` is Redshift's
            # maximum width and matches Spark's unbounded string semantics.
            cast_expr = f"CAST({quoted} AS VARCHAR(65535))"

        # TRIM keeps Stage-1 whitespace-symmetric with the row-hash compare path
        # and the Spark target serializer.
        trimmed = f"TRIM({cast_expr})"
        if self._treat_empty_as_null:
            return f"COALESCE(NULLIF({trimmed}, ''), '{NULL_SENTINEL}')"
        return f"COALESCE({trimmed}, '{NULL_SENTINEL}')"

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
        from_table = f"{_quote_redshift_identifier(schema)}.{_quote_redshift_identifier(table)}"
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
        from_table = f"{_quote_redshift_identifier(schema)}.{_quote_redshift_identifier(table)}"
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
