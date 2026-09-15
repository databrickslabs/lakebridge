import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from functools import partial

import sqlglot
import sqlglot.expressions as exp
from sqlglot import Dialect, parse_one

from databricks.labs.lakebridge.reconcile.connectors.data_source import DataSource
from databricks.labs.lakebridge.reconcile.query_builder.expression_generator import (
    anonymous,
    array_sort,
    array_to_string,
    build_column_no_alias,
    coalesce,
    transform_expression,
    trim,
)
from databricks.labs.lakebridge.reconcile.recon_config import Schema, Table
from databricks.labs.lakebridge.transpiler.sqlglot.dialect_utils import SQLGLOT_DIALECTS, get_dialect

logger = logging.getLogger(__name__)


#: Per-dialect, per-datatype transforms applied to a column before it is
#: hashed or sampled, so the same value serializes identically across engines.
_DATATYPE_TRANSFORM_MAPPING: dict[str, dict[str, list[partial[exp.Expression]]]] = {
    "universal": {"default": [partial(coalesce, default='_null_recon_', is_string=True), partial(trim)]},
    "bigquery": {
        # TODO: add numbers handling
        # Timestamps are formatted rather than cast: BigQuery's CAST AS STRING appends a UTC offset and
        # pads fractional seconds, which changes the row hash. %E*S matches Spark's rendering.
        "default": [partial(anonymous, func="COALESCE(TRIM(CAST({} AS STRING)), '_null_recon_')")],
        exp.DataType.Type.TIMESTAMPTZ.value: [
            partial(
                anonymous,
                func="COALESCE(FORMAT_TIMESTAMP('%F %H:%M:%E*S', {}, 'UTC'), '_null_recon_')",
            )
        ],
        # A BigQuery DATETIME parses as TIMESTAMP; a user-supplied `timestamp_ntz` parses as
        # TIMESTAMPNTZ. Both spellings reach the mapping, so both are keyed.
        exp.DataType.Type.TIMESTAMP.value: [
            partial(anonymous, func="COALESCE(FORMAT_DATETIME('%F %H:%M:%E*S', {}), '_null_recon_')")
        ],
        exp.DataType.Type.TIMESTAMPNTZ.value: [
            partial(anonymous, func="COALESCE(FORMAT_DATETIME('%F %H:%M:%E*S', {}), '_null_recon_')")
        ],
    },
    "snowflake": {exp.DataType.Type.ARRAY.value: [partial(array_to_string), partial(array_sort)]},
    "oracle": {
        exp.DataType.Type.NCHAR.value: [
            partial(anonymous, func="NVL(TRIM(TO_CHAR({})),'_null_recon_')", dialect=get_dialect("oracle"))
        ],
        exp.DataType.Type.CHAR.value: [
            partial(anonymous, func="NVL(TRIM(TO_CHAR({})),'_null_recon_')", dialect=get_dialect("oracle"))
        ],
    },
    "databricks": {
        exp.DataType.Type.ARRAY.value: [
            partial(anonymous, func="CONCAT_WS(',', SORT_ARRAY({}))", dialect=get_dialect("databricks"))
        ],
        # Align with Redshift's ``TO_CHAR(ts, 'YYYY-MM-DD HH24:MI:SS.US')`` so the per-row
        # SHA2 inputs are byte-identical for Redshift -> Databricks reconciles. Only
        # byte-identical when the counterpart also pins, so it is gated via
        # ``_COUNTERPART_PINNED_TYPES``: against a non-pinning source the Databricks target
        # falls back to the universal default (see ``get_transform_for_type``).
        exp.DataType.Type.TIMESTAMP.value: [
            partial(
                anonymous,
                func="COALESCE(DATE_FORMAT({}, 'yyyy-MM-dd HH:mm:ss.SSSSSS'), '_null_recon_')",
                dialect=get_dialect("databricks"),
            )
        ],
        # sqlglot's Databricks dialect maps both TIMESTAMP and TIMESTAMPTZ to this one entry
        # (Spark timestamps are instant / TIMESTAMP_LTZ). It does NOT pin a timezone here:
        # ``DATE_FORMAT`` renders in ``spark.sql.session.timeZone``, so it matches the (UTC)
        # source render only when the reconcile cluster session is UTC (the Databricks
        # default). The Redshift side pins TIMESTAMPTZ to UTC via ``AT TIME ZONE 'UTC'``.
        exp.DataType.Type.TIMESTAMPTZ.value: [
            partial(
                anonymous,
                func="COALESCE(DATE_FORMAT({}, 'yyyy-MM-dd HH:mm:ss.SSSSSS'), '_null_recon_')",
                dialect=get_dialect("databricks"),
            )
        ],
        # Redshift ``double precision`` and Databricks ``DOUBLE`` serialise to different
        # strings under the universal ``TRIM(CAST(_ AS STRING))`` default (Redshift full
        # 17-digit precision, Spark the shortest round-trip), so every double row
        # false-mismatches on a Redshift -> Databricks reconcile. Pinning both sides to a
        # fixed-scale ``DECIMAL(38,10)`` string makes the byte stream identical. Only emitted
        # when the counterpart also pins (see ``get_transform_for_type``). NaN / Infinity and
        # any finite magnitude >= 1e28 overflow ``DECIMAL(38,10)`` (28 integer digits) and
        # bypass the pin via a native string cast so the recon does not crash. The pin rounds
        # to 10 fractional digits -- an accepted precision floor.
        exp.DataType.Type.DOUBLE.value: [
            partial(
                anonymous,
                func=(
                    "COALESCE(CASE WHEN ISNAN({0}) OR {0} IN (CAST('Infinity' AS DOUBLE), "
                    "CAST('-Infinity' AS DOUBLE)) OR ABS({0}) >= 1e28 THEN CAST({0} AS STRING) "
                    "ELSE CAST(CAST({0} AS DECIMAL(38,10)) AS STRING) END, '_null_recon_')"
                ),
                dialect=get_dialect("databricks"),
            )
        ],
        # FLOAT (Redshift ``real``/``float4``, single precision) has the same
        # full-precision-vs-shortest-round-trip divergence as DOUBLE, so it is pinned
        # identically (same DECIMAL(38,10) scale -- see ``_COUNTERPART_PINNED_TYPES``).
        # Mirrors the Redshift FLOAT handler below.
        exp.DataType.Type.FLOAT.value: [
            partial(
                anonymous,
                func=(
                    "COALESCE(CASE WHEN ISNAN({0}) OR {0} IN (CAST('Infinity' AS DOUBLE), "
                    "CAST('-Infinity' AS DOUBLE)) OR ABS({0}) >= 1e28 THEN CAST({0} AS STRING) "
                    "ELSE CAST(CAST({0} AS DECIMAL(38,10)) AS STRING) END, '_null_recon_')"
                ),
                dialect=get_dialect("databricks"),
            )
        ],
    },
    "tsql": {
        "default": [partial(anonymous, func="COALESCE(TRIM(CAST({} AS VARCHAR(MAX))), '_null_recon_')")],
        exp.DataType.Type.DATE.value: [
            partial(anonymous, func="COALESCE(CONVERT(VARCHAR(10), {0}, 101), '1900-01-01')")
        ],
        exp.DataType.Type.TIME.value: [partial(anonymous, func="COALESCE(CONVERT(VARCHAR(12), {0}, 108), '00:00:00')")],
        exp.DataType.Type.DATETIME.value: [
            partial(anonymous, func="COALESCE(CONVERT(VARCHAR(23), {0}, 120), '1900-01-01 00:00:00')")
        ],
    },
    "redshift": {
        exp.DataType.Type.SUPER.value: [
            partial(anonymous, func="COALESCE(JSON_SERIALIZE({}), '_null_recon_')", dialect=get_dialect("redshift"))
        ],
        exp.DataType.Type.DATE.value: [
            partial(
                anonymous,
                func="COALESCE(TO_CHAR({}, 'YYYY-MM-DD'), '_null_recon_')",
                dialect=get_dialect("redshift"),
            )
        ],
        exp.DataType.Type.TIMESTAMP.value: [
            partial(
                anonymous,
                func="COALESCE(TO_CHAR({}, 'YYYY-MM-DD HH24:MI:SS.US'), '_null_recon_')",
                dialect=get_dialect("redshift"),
            )
        ],
        # ``AT TIME ZONE 'UTC'`` pins the render to UTC so it does not depend on the Redshift
        # session ``TIMEZONE`` setting. Without it a non-UTC session renders TIMESTAMPTZ in
        # local time and every TIMESTAMPTZ row false-mismatches against the Spark target. The
        # Databricks side renders via ``DATE_FORMAT`` in ``spark.sql.session.timeZone``, so
        # both sides emit the same UTC wall clock only when the reconcile session is UTC.
        exp.DataType.Type.TIMESTAMPTZ.value: [
            partial(
                anonymous,
                func="COALESCE(TO_CHAR({} AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS.US'), '_null_recon_')",
                dialect=get_dialect("redshift"),
            )
        ],
        # Redshift rejects every ``CAST(boolean AS VARCHAR/TEXT)`` form, and the universal
        # ``TRIM(...)`` default becomes ``btrim(boolean)`` (function-not-found), crashing any
        # recon with a boolean column. CASE WHEN emits the same lowercase 'true'/'false' that
        # Spark's ``cast(boolean AS string)`` produces, keeping row hashes byte-identical.
        exp.DataType.Type.BOOLEAN.value: [
            partial(
                anonymous,
                func="COALESCE(CASE WHEN {0} THEN 'true' WHEN NOT {0} THEN 'false' ELSE NULL END, '_null_recon_')",
                dialect=get_dialect("redshift"),
            )
        ],
        # Mirror of the Databricks DOUBLE handler: pin to a fixed-scale ``DECIMAL(38,10)``
        # string so Redshift's full-precision render and Spark's shortest round-trip agree.
        # NaN / Infinity and finite magnitude >= 1e28 overflow ``DECIMAL(38,10)`` (Redshift
        # raises "numeric field overflow") and bypass the pin via a native VARCHAR cast.
        # Postgres-family engines treat NaN as equal to itself, so the ``IN`` check is
        # well-defined. Gated on the counterpart via ``get_transform_for_type``.
        exp.DataType.Type.DOUBLE.value: [
            partial(
                anonymous,
                func=(
                    "COALESCE(CASE WHEN {0} IN (CAST('NaN' AS DOUBLE PRECISION), "
                    "CAST('Infinity' AS DOUBLE PRECISION), CAST('-Infinity' AS DOUBLE PRECISION)) "
                    "OR ABS({0}) >= 1e28 "
                    "THEN CAST({0} AS VARCHAR) "
                    "ELSE CAST(CAST({0} AS DECIMAL(38,10)) AS VARCHAR) END, '_null_recon_')"
                ),
                dialect=get_dialect("redshift"),
            )
        ],
        # Redshift ``real``/``float4`` (single precision) has the same divergence as
        # ``double precision`` above, pinned identically -- same DECIMAL(38,10) scale as
        # DOUBLE so a float/double column pair stays byte-identical across engines.
        # Mirrors the Databricks FLOAT handler above.
        exp.DataType.Type.FLOAT.value: [
            partial(
                anonymous,
                func=(
                    "COALESCE(CASE WHEN {0} IN (CAST('NaN' AS DOUBLE PRECISION), "
                    "CAST('Infinity' AS DOUBLE PRECISION), CAST('-Infinity' AS DOUBLE PRECISION)) "
                    "OR ABS({0}) >= 1e28 "
                    "THEN CAST({0} AS VARCHAR) "
                    "ELSE CAST(CAST({0} AS DECIMAL(38,10)) AS VARCHAR) END, '_null_recon_')"
                ),
                dialect=get_dialect("redshift"),
            )
        ],
    },
    "teradata": {
        exp.DataType.Type.DATE.value: [
            partial(
                anonymous,
                func="COALESCE(CAST(CAST({} AS DATE FORMAT 'YYYY-MM-DD') AS VARCHAR(10)), '_null_recon_')",
                dialect=get_dialect("teradata"),
            )
        ],
        exp.DataType.Type.TIMESTAMP.value: [
            partial(
                anonymous,
                func="COALESCE(CAST(CAST({} AS TIMESTAMP(6) FORMAT 'YYYY-MM-DDBHH:MI:SS.S(6)') AS VARCHAR(26)), '_null_recon_')",
                dialect=get_dialect("teradata"),
            )
        ],
        exp.DataType.Type.TIMESTAMPTZ.value: [
            partial(
                anonymous,
                func="COALESCE(CAST(CAST({} AS TIMESTAMP(6) WITH TIME ZONE) AS VARCHAR(32)), '_null_recon_')",
                dialect=get_dialect("teradata"),
            )
        ],
        exp.DataType.Type.TIME.value: [
            partial(
                anonymous,
                func="COALESCE(CAST({} AS VARCHAR(15)), '_null_recon_')",
                dialect=get_dialect("teradata"),
            )
        ],
        exp.DataType.Type.JSON.value: [
            partial(
                anonymous,
                func="COALESCE(CAST({} AS VARCHAR(32000)), '_null_recon_')",
                dialect=get_dialect("teradata"),
            )
        ],
        exp.DataType.Type.XML.value: [
            partial(
                anonymous,
                func="COALESCE(CAST({} AS VARCHAR(32000)), '_null_recon_')",
                dialect=get_dialect("teradata"),
            )
        ],
    },
}


def _dialect_key(dialect: Dialect) -> str:
    """The ``_DATATYPE_TRANSFORM_MAPPING`` key for ``dialect``.

    Several dialect names can share one sqlglot ``Dialect`` object (e.g.
    ``netezza``/``postgresql``/``vertica`` all resolve to Postgres, ``tsql``/``mssql``/
    ``synapse`` to T-SQL), so a bare ``keys[0]`` can pick a synonym that carries no
    overrides and shadow the one that does. Prefer a name that actually has a mapping
    entry; fall back to the first match otherwise (and to ``universal`` when unknown).
    """
    keys = [key for key, value in SQLGLOT_DIALECTS.items() if value == dialect]
    for key in keys:
        if key in _DATATYPE_TRANSFORM_MAPPING:
            return key
    return keys[0] if keys else "universal"


# Dialects whose mapping pins certain types to a fixed, cross-engine-identical
# serialization (DOUBLE/FLOAT -> ``DECIMAL(38,10)`` string; TIMESTAMP/TIMESTAMPTZ ->
# ``...HH:mm:ss.SSSSSS`` microsecond string). Such a pin only yields a byte-identical
# hash when *both* engines pin the same way -- currently only Redshift <-> Databricks.
_PINNING_DIALECTS = frozenset({"redshift", "databricks"})

# Types served by a pinned handler that is only byte-identical when the reconcile
# counterpart also pins the same type. Against a non-pinning counterpart (e.g. a
# Snowflake/Oracle/TSQL/BigQuery source reconciling into a Databricks target) these fall
# back to the universal default so both sides still serialise identically.
# FLOAT (Redshift ``real``/``float4``, single precision) shares DOUBLE's root cause:
# Redshift renders it full-precision while Spark emits the shortest round-trip, so it is
# pinned the same way -- and to the *same* ``DECIMAL(38,10)`` scale as DOUBLE, because
# sqlglot resolves Redshift ``float`` to DOUBLE but Databricks ``float`` to FLOAT; a
# different scale would make a float/double column pair diverge across engines.
_COUNTERPART_PINNED_TYPES = frozenset(
    {
        exp.DataType.Type.DOUBLE.value,
        exp.DataType.Type.FLOAT.value,
        exp.DataType.Type.TIMESTAMP.value,
        exp.DataType.Type.TIMESTAMPTZ.value,
    }
)


def get_transform_for_type(
    datatype: str, source: Dialect, counterpart: Dialect | None = None
) -> list[partial[exp.Expression]]:
    """Resolve the per-type transforms for ``datatype`` on ``source``.

    Single definition of the dialect/type -> transform lookup, shared by the row-hash
    query builder (via ``RuleBasedColumnTransformer``) and the fingerprint pre-check, so
    the two cannot serialise the same column type differently. Falls back to the dialect
    ``default`` and then the universal ``default``.

    ``counterpart`` is the *other* engine in the reconcile (target when ``source`` is the
    source layer, and vice versa). It gates the ``_COUNTERPART_PINNED_TYPES`` handlers
    (DOUBLE, TIMESTAMP, TIMESTAMPTZ): those normalise a value to a fixed, cross-engine
    string that is only byte-identical when *both* engines pin the same way (currently
    Redshift <-> Databricks). When the counterpart does NOT pin (e.g. a
    BigQuery/Snowflake/Oracle/TSQL source into a Databricks target), fall back to the
    universal default so both sides serialise the value the same way -- otherwise every
    such row false-mismatches. A ``None`` counterpart (unknown, e.g. the Redshift-only
    fingerprint path) keeps the pin.
    """
    source_dialect = _dialect_key(source)
    source_mapping = _DATATYPE_TRANSFORM_MAPPING.get(source_dialect, {})

    parsed = datatype
    try:
        parsed = exp.DataType.build(datatype, source).this.value
    except sqlglot.errors.ParseError:
        logger.warning(f"Could not parse datatype {datatype} for source {source_dialect}")

    if (
        parsed in _COUNTERPART_PINNED_TYPES
        and source_dialect in _PINNING_DIALECTS
        and counterpart is not None
        and _dialect_key(counterpart) not in _PINNING_DIALECTS
    ):
        return _DATATYPE_TRANSFORM_MAPPING["universal"]["default"]

    exact_match = source_mapping.get(parsed)
    if exact_match is not None:
        return exact_match
    dialect_default = source_mapping.get("default")
    if dialect_default is not None:
        return dialect_default
    return _DATATYPE_TRANSFORM_MAPPING["universal"]["default"]


def serialize_column_for_hash(column_ref: str, datatype: str, source: Dialect) -> str:
    """Render one column's hash-serialised SQL for ``source`` dialect.

    ``column_ref`` is the *source-normalised* (already dialect-quoted) column reference,
    exactly as the row-hash path feeds ``build_column_no_alias`` -- e.g. the double-quoted
    form for Redshift or the backtick-quoted form for Databricks. Applying the dialect/type
    transforms here means the fingerprint source (Redshift) and target (Databricks)
    serialisers produce a per-column byte stream identical to the row-hash compare path by
    construction, rather than via three hand-maintained copies kept in sync by tests.
    """
    node = build_column_no_alias(this=column_ref)
    transformed = transform_expression(node, get_transform_for_type(datatype, source))
    return transformed.sql(dialect=source)


@dataclass(frozen=True)
class TransformedColumn:
    """A column expression after transformation.

    ``original_type`` is the column's declared type, or ``None`` when a user
    transformation changed the column -- the sampler uses it to decide whether to
    cast a reconstructed literal back to the column's type.
    """

    column: exp.Expression
    ansi_name: str
    original_type: str | None


@dataclass(frozen=True)
class ReconcileLayer:
    """One side (source or target) of a reconciliation: its data source (for
    identifier normalization), SQL dialect, and column schema."""

    data_source: DataSource
    dialect: Dialect
    schema: list[Schema]


class ColumnTransformer(ABC):
    """Transforms reconciliation columns into the form used for comparison.

    An implementation holds both the source and target sides, so a column can be
    transformed with knowledge of its counterpart on the other side.
    """

    @abstractmethod
    def transform(self, columns: list[exp.Expression], layer: str) -> list[TransformedColumn]:
        """Apply user overrides, then default per-type transforms to the rest."""

    @abstractmethod
    def transform_user(self, columns: list[exp.Expression], layer: str) -> list[TransformedColumn]:
        """Apply only user-configured SQL overrides; a no-op when none are configured."""


class RuleBasedColumnTransformer(ColumnTransformer):
    """Transforms columns from config rules: user overrides plus a per-dialect,
    per-datatype transform table (`_DATATYPE_TRANSFORM_MAPPING`)."""

    def __init__(
        self,
        source: ReconcileLayer,
        target: ReconcileLayer,
        table_conf: Table,
    ):
        self._sides = {"source": source, "target": target}
        self._table_conf = table_conf

    def transform(self, columns: list[exp.Expression], layer: str) -> list[TransformedColumn]:
        """Transform each column into its comparison form for `layer`.

        1. Look up the user override SQL per column (`_user_overrides`).
        2. Map the remaining columns to their declared type; a user-overridden
           column is excluded, so its `original_type` is None and the sampler
           leaves the (now retyped) value uncast.
        3. Per column, substitute the user override if any, then normalize
           whatever is left to its declared type.
        """
        side = self._sides[layer]
        # The other side of the reconcile, so a counterpart-gated per-type pin
        # (see ``get_transform_for_type``) is only emitted when both engines pin.
        counterpart = self._sides["target" if layer == "source" else "source"]
        user_overrides = self._get_user_overrides(layer)
        types_by_name = {
            s.ansi_normalized_column_name: s.data_type
            for s in side.schema
            if s.ansi_normalized_column_name not in user_overrides
        }
        transformed = []
        for column in columns:
            ansi_name = self._get_referenced_column_name(column, side)
            new_column = column.transform(self._substitute_user_sql, side, user_overrides).transform(
                self._normalize_by_type, side, types_by_name, counterpart.dialect
            )
            transformed.append(TransformedColumn(new_column, ansi_name, types_by_name.get(ansi_name)))
        return transformed

    def transform_user(self, columns: list[exp.Expression], layer: str) -> list[TransformedColumn]:
        """Substitute only user overrides; carry the declared type for un-overridden columns."""
        side = self._sides[layer]
        user_overrides = self._get_user_overrides(layer)
        types_by_name = {s.ansi_normalized_column_name: s.data_type for s in side.schema}
        transformed = []
        for column in columns:
            ansi_name = self._get_referenced_column_name(column, side)
            new_column = column.transform(self._substitute_user_sql, side, user_overrides)
            original_type = None if ansi_name in user_overrides else types_by_name.get(ansi_name)
            transformed.append(TransformedColumn(new_column, ansi_name, original_type))
        return transformed

    def _get_user_overrides(self, layer: str) -> dict[str, str]:
        """Ansi column name -> the SQL the user configured to replace it, for `layer`.

        Source columns key on the transformation's own name; target columns key
        through the column mapping, since a mapped column has a different name there.
        """
        side = self._sides[layer]
        transformations = self._table_conf.transformations or []
        if layer == "source":
            return {
                t.column_name: (t.source or side.data_source.normalize_identifier(t.column_name).source_normalized)
                for t in transformations
            }
        return {
            self._table_conf.get_layer_src_to_tgt_col_mapping(t.column_name, layer): (
                t.target or self._table_conf.get_layer_src_to_tgt_col_mapping(t.column_name, layer)
            )
            for t in transformations
        }

    def _get_referenced_column_name(self, column: exp.Expression, side: ReconcileLayer) -> str:
        """The ansi name of the column this expression refers to, used to identify
        it for type lookup and sampling; "" if the expression references no column."""
        for node in column.find_all(exp.Column):
            return side.data_source.normalize_identifier(node.name).ansi_normalized
        return ""

    @staticmethod
    def _substitute_user_sql(
        node: exp.Expression, side: ReconcileLayer, user_overrides: dict[str, str]
    ) -> exp.Expression:
        """Tree visitor: swap a column node for the user's override SQL, if one exists."""
        if isinstance(node, exp.Column) and user_overrides:
            normalized_column = side.data_source.normalize_identifier(node.name)
            ansi_name = normalized_column.ansi_normalized
            if ansi_name in user_overrides:
                return parse_one(user_overrides.get(ansi_name, normalized_column.source_normalized), read=side.dialect)
        return node

    def _normalize_by_type(
        self,
        node: exp.Expression,
        side: ReconcileLayer,
        types_by_name: dict[str, str],
        counterpart_dialect: Dialect,
    ) -> exp.Expression:
        """Tree visitor: wrap a column node so its value serializes identically
        across engines, per the column's declared type. ``counterpart_dialect`` gates
        counterpart-pinned types (see ``get_transform_for_type``)."""
        if isinstance(node, exp.Column):
            ansi_name = side.data_source.normalize_identifier(node.name).ansi_normalized
            if ansi_name in types_by_name:
                normalization = get_transform_for_type(types_by_name[ansi_name], side.dialect, counterpart_dialect)
                return transform_expression(node, normalization)
        return node
