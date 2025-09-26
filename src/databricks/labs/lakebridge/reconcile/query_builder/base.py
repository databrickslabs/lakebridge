import logging
from abc import ABC

import sqlglot.expressions as exp
from sqlglot import Dialect, parse_one

from databricks.labs.lakebridge.reconcile.connectors.data_source import DataSource
from databricks.labs.lakebridge.reconcile.connectors.dialect_utils import DialectUtils
from databricks.labs.lakebridge.reconcile.connectors.oracle import OracleDataSource
from databricks.labs.lakebridge.reconcile.connectors.snowflake import SnowflakeDataSource
from databricks.labs.lakebridge.reconcile.exception import InvalidInputException
from databricks.labs.lakebridge.reconcile.query_builder.expression_generator import (
    DataType_transform_mapping,
    transform_expression,
    build_column,
)
from databricks.labs.lakebridge.reconcile.recon_config import Schema, Table, Aggregate
from databricks.labs.lakebridge.transpiler.sqlglot.dialect_utils import get_dialect, SQLGLOT_DIALECTS

logger = logging.getLogger(__name__)


class QueryBuilder(ABC):
    def __init__(self, table_conf: Table, schema: list[Schema], layer: str, engine: Dialect, data_source: DataSource):
        self._table_conf = table_conf
        self._schema = schema
        self._layer = layer
        self._engine = engine
        self._data_source = data_source

    @property
    def engine(self) -> Dialect:
        return self._engine if self.layer == "source" else get_dialect("databricks")

    @property
    def layer(self) -> str:
        return self._layer

    @property
    def schema(self) -> list[Schema]:
        return self._schema

    @property
    def table_conf(self) -> Table:
        return self._table_conf

    @property
    def select_columns(self) -> set[str]:
        return self.table_conf.get_select_columns(self._schema, self._layer)

    @property
    def threshold_columns(self) -> set[str]:
        return self.table_conf.get_threshold_columns(self._layer)

    @property
    def join_columns(self) -> set[str] | None:
        return self.table_conf.get_join_columns(self._layer)

    @property
    def drop_columns(self) -> set[str]:
        return self._table_conf.get_drop_columns(self._layer)

    @property
    def partition_column(self) -> set[str]:
        return self._table_conf.get_partition_column(self._layer)

    @property
    def filter(self) -> str | None:
        return self._table_conf.get_filter(self._layer)

    @property
    def user_transformations(self) -> dict[str, str]:
        if self._table_conf.transformations:
            if self.layer == "source":
                return {
                    trans.column_name: (
                        trans.source
                        if trans.source
                        else self._data_source.normalize_identifier(trans.column_name).source_normalized
                    )
                    for trans in self._table_conf.transformations
                }
            return {
                self._table_conf.get_layer_src_to_tgt_col_mapping(trans.column_name, self.layer): (
                    trans.target
                    if trans.target
                    else self._table_conf.get_layer_src_to_tgt_col_mapping(trans.column_name, self.layer)
                )
                for trans in self._table_conf.transformations
            }
        return {}

    @property
    def aggregates(self) -> list[Aggregate] | None:
        return self.table_conf.aggregates

    def add_transformations(self, aliases: list[exp.Expression], source: Dialect) -> list[exp.Expression]:
        if self.user_transformations:
            alias_with_user_transforms = self._apply_user_transformation(aliases)
            default_transform_schema: list[Schema] = list(
                filter(lambda sch: sch.column_name not in self.user_transformations.keys(), self.schema)
            )
            return self._apply_default_transformation(alias_with_user_transforms, default_transform_schema, source)
        return self._apply_default_transformation(aliases, self.schema, source)

    def _apply_user_transformation(self, aliases: list[exp.Expression]) -> list[exp.Expression]:
        with_transform = []
        for alias in aliases:
            with_transform.append(alias.transform(self._user_transformer, self.user_transformations))
        return with_transform

    def _user_transformer(self, node: exp.Expression, user_transformations: dict[str, str]) -> exp.Expression:
        if isinstance(node, exp.Column) and user_transformations:
            normalized_column = self._data_source.normalize_identifier(node.name)
            ansi_name = normalized_column.ansi_normalized
            if ansi_name in user_transformations.keys():
                return parse_one(
                    user_transformations.get(ansi_name, normalized_column.source_normalized), read=self.engine
                )
        return node

    def _apply_default_transformation(
        self, aliases: list[exp.Expression], schema: list[Schema], source: Dialect
    ) -> list[exp.Expression]:
        with_transform = []
        for alias in aliases:
            with_transform.append(alias.transform(self._default_transformer, schema, source))
        return with_transform

    def _default_transformer(self, node: exp.Expression, schema: list[Schema], source: Dialect) -> exp.Expression:

        def _get_transform(datatype: str):
            source_dialects = [source_key for source_key, dialect in SQLGLOT_DIALECTS.items() if dialect == source]
            source_dialect = source_dialects[0] if source_dialects else "universal"

            source_mapping = DataType_transform_mapping.get(source_dialect, {})

            if source_mapping.get(datatype.upper()) is not None:
                return source_mapping.get(datatype.upper())
            if source_mapping.get("default") is not None:
                return source_mapping.get("default")

            return DataType_transform_mapping.get("universal", {}).get("default")

        schema_dict = {v.column_name: v.data_type for v in schema}
        if isinstance(node, exp.Column):
            normalized_column = self._data_source.normalize_identifier(node.name)
            ansi_name = normalized_column.ansi_normalized
            if ansi_name in schema_dict.keys():
                transform = _get_transform(schema_dict.get(ansi_name, normalized_column.source_normalized))
                return transform_expression(node, transform)
        return node

    def _validate(self, field: set[str] | list[str] | None, message: str):
        if field is None:
            message = f"Exception for {self.table_conf.target_name} target table in {self.layer} layer --> {message}"
            logger.error(message)
            raise InvalidInputException(message)

    def _build_column_with_alias(self, column: str):
        return build_column(
            this=self._build_column_name_source_normalized(column),
            alias=DialectUtils.unnormalize_identifier(
                self.table_conf.get_layer_tgt_to_src_col_mapping(column, self.layer)
            ),
            quoted=True and self._is_add_quotes,
        )

    def _build_column_name_source_normalized(self, column: str):
        return self._data_source.normalize_identifier(column).source_normalized

    def _build_alias_source_normalized(self, column: str):
        return self._data_source.normalize_identifier(
            self.table_conf.get_layer_tgt_to_src_col_mapping(column, self.layer)
        ).source_normalized

    @property
    def _is_add_quotes(self):
        # TODO: In Oracle and Snowflake, quoted identifiers are case-sensitive,
        # it is disabled for now till we have a proper strategy to handle it.
        return not isinstance(self._data_source, (OracleDataSource, SnowflakeDataSource))
