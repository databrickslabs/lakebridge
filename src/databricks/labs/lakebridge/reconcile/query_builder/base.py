import logging
from abc import ABC

import sqlglot.expressions as exp
from sqlglot import Dialect, parse_one

from databricks.labs.lakebridge.reconcile.exception import InvalidInputException
from databricks.labs.lakebridge.reconcile.query_builder.expression_generator import (
    DataType_transform_mapping,
    transform_expression,
)
from databricks.labs.lakebridge.reconcile.recon_config import Schema, Table, Aggregate
from databricks.labs.lakebridge.transpiler.sqlglot.dialect_utils import get_dialect, SQLGLOT_DIALECTS

logger = logging.getLogger(__name__)


class QueryBuilder(ABC):
    def __init__(
        self,
        table_conf: Table,
        schema: list[Schema],
        layer: str,
        engine: Dialect,
    ):
        self._table_conf = table_conf
        self._schema = schema
        self._layer = layer
        self._engine = engine

    @property
    def engine(self) -> Dialect:
        return self._engine

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
        return self._table_conf.get_transformation_dict(self._layer)

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
            dialect = self.engine if self.layer == "source" else get_dialect("databricks")
            column_name = node.name
            if column_name in user_transformations.keys():
                return parse_one(user_transformations.get(column_name, column_name), read=dialect)
        return node

    def _apply_default_transformation(
        self, aliases: list[exp.Expression], schema: list[Schema], source: Dialect
    ) -> list[exp.Expression]:
        with_transform = []
        for alias in aliases:
            with_transform.append(alias.transform(self._default_transformer, schema, source))
        return with_transform

    @staticmethod
    def _default_transformer(node: exp.Expression, schema: list[Schema], source: Dialect) -> exp.Expression:

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
            column_name = node.name
            if column_name in schema_dict.keys():
                transform = _get_transform(schema_dict.get(column_name, column_name))
                return transform_expression(node, transform)
        return node

    def _validate(self, field: set[str] | list[str] | None, message: str):
        if field is None:
            message = f"Exception for {self.table_conf.target_name} target table in {self.layer} layer --> {message}"
            logger.error(message)
            raise InvalidInputException(message)
