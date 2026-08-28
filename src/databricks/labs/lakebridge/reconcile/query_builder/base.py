import logging
from abc import ABC

from sqlglot import Dialect

from databricks.labs.lakebridge.reconcile.connectors.data_source import DataSource
from databricks.labs.lakebridge.reconcile.connectors.dialect_utils import DialectUtils
from databricks.labs.lakebridge.reconcile.connectors.oracle import OracleDataSource
from databricks.labs.lakebridge.reconcile.connectors.snowflake import SnowflakeDataSource
from databricks.labs.lakebridge.reconcile.exception import InvalidInputException
from databricks.labs.lakebridge.reconcile.query_builder.column_transformer import ColumnTransformer
from databricks.labs.lakebridge.reconcile.query_builder.expression_generator import build_column
from databricks.labs.lakebridge.reconcile.recon_config import Aggregate, Schema, Table
from databricks.labs.lakebridge.transpiler.sqlglot.dialect_utils import get_dialect

logger = logging.getLogger(__name__)


class QueryBuilder(ABC):
    def __init__(
        self,
        table_conf: Table,
        schema: list[Schema],
        layer: str,
        source_engine: Dialect,
        data_source: DataSource,
        transformer: ColumnTransformer,
    ):
        self._table_conf = table_conf
        self._schema = schema
        self._layer = layer
        self._source_engine = source_engine
        self._data_source = data_source
        self._transformer = transformer

    @property
    def engine(self) -> Dialect:
        return self._source_engine if self.layer == "source" else get_dialect("databricks")

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
    def aggregates(self) -> list[Aggregate] | None:
        return self.table_conf.aggregates

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

    def _unnormalize_identifier(self, identifier: str):
        """
        Convert the identifier to its unnormalized form.
        We use ansi because the identifier might be source normalized
        """
        return DialectUtils.unnormalize_identifier(self._data_source.normalize_identifier(identifier).ansi_normalized)

    def _build_alias_source_normalized(self, column: str):
        return self._data_source.normalize_identifier(
            self.table_conf.get_layer_tgt_to_src_col_mapping(column, self.layer)
        ).source_normalized

    @property
    def _is_add_quotes(self) -> bool:
        # TODO: In Oracle and Snowflake, quoted identifiers are case-sensitive,
        # it is disabled for now till we have a proper strategy to handle it.
        return not isinstance(self._data_source, (OracleDataSource, SnowflakeDataSource))
