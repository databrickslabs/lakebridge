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
        exp.DataType.Type.TIMESTAMPTZ.value: [
            partial(
                anonymous,
                func="COALESCE(TO_CHAR({}, 'YYYY-MM-DD HH24:MI:SS.US'), '_null_recon_')",
                dialect=get_dialect("redshift"),
            )
        ],
        exp.DataType.Type.BOOLEAN.value: [
            partial(
                anonymous,
                func="COALESCE(CASE WHEN {0} THEN 'true' WHEN NOT {0} THEN 'false' ELSE NULL END, '_null_recon_')",
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


_redshift_timestamp = partial(
    anonymous,
    func="COALESCE(TO_CHAR({}, 'YYYY-MM-DD HH24:MI:SS.US'), '_null_recon_')",
    dialect=get_dialect("redshift"),
)
_redshift_timestamptz = partial(
    anonymous,
    func="COALESCE(TO_CHAR({} AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS.US'), '_null_recon_')",
    dialect=get_dialect("redshift"),
)
_redshift_float = partial(
    anonymous,
    func="COALESCE(CASE WHEN CAST({0} AS VARCHAR) IN ('NaN', 'Infinity', '-Infinity') OR ABS({0}) >= 1e28 "
    "THEN CAST({0} AS VARCHAR) ELSE CAST(CAST({0} AS DECIMAL(38,10)) AS VARCHAR) END, '_null_recon_')",
    dialect=get_dialect("redshift"),
)
_databricks_timestamp = partial(
    anonymous,
    func="COALESCE(DATE_FORMAT({}, 'yyyy-MM-dd HH:mm:ss.SSSSSS'), '_null_recon_')",
    dialect=get_dialect("databricks"),
)
_databricks_float = partial(
    anonymous,
    func="COALESCE(CASE WHEN ISNAN({0}) OR {0} IN (CAST('Infinity' AS DOUBLE), CAST('-Infinity' AS DOUBLE)) "
    "OR ABS({0}) >= 1e28 THEN CAST({0} AS STRING) "
    "ELSE CAST(CAST({0} AS DECIMAL(38,10)) AS STRING) END, '_null_recon_')",
    dialect=get_dialect("databricks"),
)

_Transforms = list[partial[exp.Expression]]

#: Transforms for a source column and its target counterpart, keyed by (source dialect, target dialect)
#: then (source type, target type); takes precedence over `_DATATYPE_TRANSFORM_MAPPING`.
_PAIRED_TRANSFORM_MAPPING: dict[tuple[str, str], dict[tuple[str, str], tuple[_Transforms, _Transforms]]] = {
    ("redshift", "databricks"): {
        (exp.DataType.Type.TIMESTAMP.value, exp.DataType.Type.TIMESTAMP.value): (
            [_redshift_timestamp],
            [_databricks_timestamp],
        ),
        (exp.DataType.Type.TIMESTAMP.value, exp.DataType.Type.TIMESTAMPTZ.value): (
            [_redshift_timestamp],
            [_databricks_timestamp],
        ),
        (exp.DataType.Type.TIMESTAMPTZ.value, exp.DataType.Type.TIMESTAMP.value): (
            [_redshift_timestamptz],
            [_databricks_timestamp],
        ),
        (exp.DataType.Type.TIMESTAMPTZ.value, exp.DataType.Type.TIMESTAMPTZ.value): (
            [_redshift_timestamptz],
            [_databricks_timestamp],
        ),
        (exp.DataType.Type.DOUBLE.value, exp.DataType.Type.DOUBLE.value): (
            [_redshift_float],
            [_databricks_float],
        ),
        (exp.DataType.Type.DOUBLE.value, exp.DataType.Type.FLOAT.value): (
            [_redshift_float],
            [_databricks_float],
        ),
        (exp.DataType.Type.FLOAT.value, exp.DataType.Type.DOUBLE.value): (
            [_redshift_float],
            [_databricks_float],
        ),
        (exp.DataType.Type.FLOAT.value, exp.DataType.Type.FLOAT.value): (
            [_redshift_float],
            [_databricks_float],
        ),
    },
}


def _dialect_name(dialect: Dialect) -> str:
    dialect_names = [name for name, registered in SQLGLOT_DIALECTS.items() if registered == dialect]
    return dialect_names[0] if dialect_names else "universal"


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
class ColumnPair:
    """A source column and the target column it is reconciled against."""

    source: exp.Expression
    target: exp.Expression


@dataclass(frozen=True)
class TransformPair:
    """A source column and its target counterpart, each transformed into its comparison form."""

    source: TransformedColumn
    target: TransformedColumn

    def for_layer(self, layer: str) -> TransformedColumn:
        return self.source if layer == "source" else self.target


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
    def transform(self, columns: list[ColumnPair]) -> list[TransformPair]:
        """Apply user overrides, then default per-type transforms to the rest, to both columns of each pair."""

    @abstractmethod
    def transform_user(self, columns: list[exp.Expression], layer: str) -> list[TransformedColumn]:
        """Apply only user-configured SQL overrides; a no-op when none are configured."""


class RuleBasedColumnTransformer(ColumnTransformer):
    """Transforms columns from config rules: user overrides plus per-datatype transform
    tables, per dialect pair (`_PAIRED_TRANSFORM_MAPPING`) and per dialect (`_DATATYPE_TRANSFORM_MAPPING`)."""

    def __init__(
        self,
        source: ReconcileLayer,
        target: ReconcileLayer,
        table_conf: Table,
    ):
        self._sides = {"source": source, "target": target}
        self._table_conf = table_conf
        self._paired_mapping = _PAIRED_TRANSFORM_MAPPING.get(
            (_dialect_name(source.dialect), _dialect_name(target.dialect)), {}
        )

    def transform(self, columns: list[ColumnPair]) -> list[TransformPair]:
        """Transform both columns of each pair into their comparison form.

        1. Look up the user override SQL per column (`_user_overrides`).
        2. Map the remaining columns to their declared type; a user-overridden
           column is excluded, so its `original_type` is None and the sampler
           leaves the (now retyped) value uncast.
        3. Look up each pair's (source type, target type) in `_PAIRED_TRANSFORM_MAPPING`.
        4. Per column, substitute the user override if any, then normalize
           whatever is left with its pair's transforms, else its declared type's.
        """
        types_by_layer = {layer: self._types_by_name(layer) for layer in self._sides}
        paired_by_layer = self._paired_transforms_by_name(columns, types_by_layer)
        sources = self._transform_layer(
            [pair.source for pair in columns], "source", types_by_layer["source"], paired_by_layer["source"]
        )
        targets = self._transform_layer(
            [pair.target for pair in columns], "target", types_by_layer["target"], paired_by_layer["target"]
        )
        return [TransformPair(source, target) for source, target in zip(sources, targets)]

    def _transform_layer(
        self,
        columns: list[exp.Expression],
        layer: str,
        types_by_name: dict[str, str],
        paired_by_name: dict[str, _Transforms],
    ) -> list[TransformedColumn]:
        """Substitute user overrides, then normalize by type, for `layer`'s column of each pair."""
        side = self._sides[layer]
        user_overrides = self._get_user_overrides(layer)
        transformed = []
        for column in columns:
            ansi_name = self._get_referenced_column_name(column, side)
            new_column = column.transform(self._substitute_user_sql, side, user_overrides).transform(
                self._normalize_by_type, side, types_by_name, paired_by_name
            )
            transformed.append(TransformedColumn(new_column, ansi_name, types_by_name.get(ansi_name)))
        return transformed

    def _types_by_name(self, layer: str) -> dict[str, str]:
        """Ansi column name -> declared type on `layer`, excluding user-overridden columns."""
        user_overrides = self._get_user_overrides(layer)
        return {
            s.ansi_normalized_column_name: s.data_type
            for s in self._sides[layer].schema
            if s.ansi_normalized_column_name not in user_overrides
        }

    def _paired_transforms_by_name(
        self, columns: list[ColumnPair], types_by_layer: dict[str, dict[str, str]]
    ) -> dict[str, dict[str, _Transforms]]:
        """Ansi column name -> its `_PAIRED_TRANSFORM_MAPPING` transforms, per layer, for pairs with an entry."""
        source, target = self._sides["source"], self._sides["target"]
        paired: dict[str, dict[str, _Transforms]] = {"source": {}, "target": {}}
        for pair in columns:
            source_name = self._get_referenced_column_name(pair.source, source)
            target_name = self._get_referenced_column_name(pair.target, target)
            if source_name not in types_by_layer["source"] or target_name not in types_by_layer["target"]:
                continue
            key = (
                self._type_value(types_by_layer["source"][source_name], source.dialect),
                self._type_value(types_by_layer["target"][target_name], target.dialect),
            )
            if key in self._paired_mapping:
                paired["source"][source_name], paired["target"][target_name] = self._paired_mapping[key]
        return paired

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
        paired_by_name: dict[str, _Transforms],
    ) -> exp.Expression:
        """Tree visitor: wrap a column node so its value serializes identically
        across engines, per its pair's transforms, else the column's declared type."""
        if isinstance(node, exp.Column):
            ansi_name = side.data_source.normalize_identifier(node.name).ansi_normalized
            if ansi_name in types_by_name:
                normalization = paired_by_name.get(ansi_name) or self._lookup_transformation_for_type(
                    types_by_name[ansi_name], side.dialect
                )
                return transform_expression(node, normalization)
        return node

    @staticmethod
    def _type_value(datatype: str, dialect: Dialect) -> str:
        try:
            return exp.DataType.build(datatype, dialect).this.value
        except sqlglot.errors.ParseError:
            return datatype

    @staticmethod
    def _lookup_transformation_for_type(datatype: str, dialect: Dialect) -> list[partial[exp.Expression]]:
        """The normalization functions for `datatype` under `dialect`: an exact-type
        match, else the dialect's default, else the universal default."""
        dialect_name = _dialect_name(dialect)
        dialect_mapping = _DATATYPE_TRANSFORM_MAPPING.get(dialect_name, {})

        parsed = datatype
        try:
            parsed = exp.DataType.build(datatype, dialect).this.value
        except sqlglot.errors.ParseError:
            logger.warning(f"Could not parse datatype {datatype} for source {dialect_name}")

        exact_match = dialect_mapping.get(parsed)
        if exact_match is not None:
            return exact_match
        dialect_default = dialect_mapping.get("default")
        if dialect_default is not None:
            return dialect_default
        return _DATATYPE_TRANSFORM_MAPPING["universal"]["default"]
