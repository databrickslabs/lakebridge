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
from databricks.labs.lakebridge.transpiler.sqlglot.dialect_utils import get_dialect, SQLGLOT_DIALECTS

logger = logging.getLogger(__name__)


#: Per-dialect, per-datatype transforms applied to a column before it is
#: hashed or sampled, so the same value serializes identically across engines.
_DATATYPE_TRANSFORM_MAPPING: dict[str, dict[str, list[partial[exp.Expression]]]] = {
    "universal": {"default": [partial(coalesce, default='_null_recon_', is_string=True), partial(trim)]},
    "bigquery": {
        # TODO: add timestamps and numbers handling
        "default": [partial(anonymous, func="COALESCE(TRIM(CAST({} AS STRING)), '_null_recon_')")],
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


@dataclass(frozen=True)
class TransformedColumn:
    """A column expression after transformation.

    ``original_type`` is the column's declared type. It is ``None`` when a user
    transformation changes the column.
    """

    column: exp.Expression
    ansi_name: str
    original_type: str | None


@dataclass(frozen=True)
class ReconcileLayer:
    """One side (source or target) of a reconciliation."""

    data_source: DataSource
    dialect: Dialect
    schema: list[Schema]


class ColumnTransformer(ABC):
    """Transforms reconciliation columns to their comparison form.

    So a source column can be transformed with knowledge of its counterpart.
    """

    @abstractmethod
    def transform(self, columns: list[exp.Expression], layer: str) -> list[TransformedColumn]:
        """Apply user overrides, then default per-type transforming to the rest."""

    @abstractmethod
    def transform_user(self, columns: list[exp.Expression], layer: str) -> list[TransformedColumn]:
        """Apply only user-configured SQL overrides; a no-op when none are configured."""


class RuleBasedColumnTransformer(ColumnTransformer):
    def __init__(
        self,
        source: ReconcileLayer,
        target: ReconcileLayer,
        table_conf: Table,
    ):
        self._sides = {"source": source, "target": target}
        self._table_conf = table_conf

    def transform(self, columns: list[exp.Expression], layer: str) -> list[TransformedColumn]:
        side = self._sides[layer]
        user_map = self._user_map(layer)
        # Type transforming never touches a user-transformed column: excluding it here means its
        # original_type is also None, so the sampler leaves the (now retyped) value uncast.
        types_by_name = {
            s.ansi_normalized_column_name: s.data_type
            for s in side.schema
            if s.ansi_normalized_column_name not in user_map
        }
        transformed = []
        for column in columns:
            ansi_name = self._column_ansi(column, side)
            new_column = column.transform(self._user_node, side, user_map).transform(
                self._type_node, side, types_by_name
            )
            transformed.append(TransformedColumn(new_column, ansi_name, types_by_name.get(ansi_name)))
        return transformed

    def transform_user(self, columns: list[exp.Expression], layer: str) -> list[TransformedColumn]:
        side = self._sides[layer]
        user_map = self._user_map(layer)
        types_by_name = {s.ansi_normalized_column_name: s.data_type for s in side.schema}
        transformed = []
        for column in columns:
            ansi_name = self._column_ansi(column, side)
            new_column = column.transform(self._user_node, side, user_map)
            original_type = None if ansi_name in user_map else types_by_name.get(ansi_name)
            transformed.append(TransformedColumn(new_column, ansi_name, original_type))
        return transformed

    def _user_map(self, layer: str) -> dict[str, str]:
        """Column ansi-name -> user-supplied SQL for `layer`, from the recon config."""
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

    def _column_ansi(self, column: exp.Expression, side: ReconcileLayer) -> str:
        for node in column.find_all(exp.Column):
            return side.data_source.normalize_identifier(node.name).ansi_normalized
        return ""

    @staticmethod
    def _user_node(node: exp.Expression, side: ReconcileLayer, user_map: dict[str, str]) -> exp.Expression:
        if isinstance(node, exp.Column) and user_map:
            normalized_column = side.data_source.normalize_identifier(node.name)
            ansi_name = normalized_column.ansi_normalized
            if ansi_name in user_map:
                return parse_one(user_map.get(ansi_name, normalized_column.source_normalized), read=side.dialect)
        return node

    def _type_node(
        self,
        node: exp.Expression,
        side: ReconcileLayer,
        types_by_name: dict[str, str],
    ) -> exp.Expression:
        if isinstance(node, exp.Column):
            ansi_name = side.data_source.normalize_identifier(node.name).ansi_normalized
            if ansi_name in types_by_name:
                transform = self._resolve_type_transform(types_by_name[ansi_name], side.dialect)
                return transform_expression(node, transform)
        return node

    @staticmethod
    def _resolve_type_transform(datatype: str, dialect: Dialect) -> list[partial[exp.Expression]]:
        dialect_names = [name for name, registered in SQLGLOT_DIALECTS.items() if registered == dialect]
        dialect_name = dialect_names[0] if dialect_names else "universal"
        dialect_mapping = _DATATYPE_TRANSFORM_MAPPING.get(dialect_name, {})

        parsed = datatype
        try:
            parsed = exp.DataType.build(datatype, dialect).this.value
        except sqlglot.errors.ParseError:
            logger.warning(f"Could not parse datatype {datatype} for source {dialect_name}")

        type_transform = dialect_mapping.get(parsed)
        if type_transform is not None:
            return type_transform
        dialect_default = dialect_mapping.get("default")
        if dialect_default is not None:
            return dialect_default
        return _DATATYPE_TRANSFORM_MAPPING["universal"]["default"]
