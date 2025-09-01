from __future__ import annotations

import logging

from dataclasses import dataclass
from collections.abc import Callable

from sqlglot import expressions as exp

from databricks.labs.lakebridge.reconcile.constants import SamplingOptionMethod, SamplingSpecificationsType

logger = logging.getLogger(__name__)

_SUPPORTED_AGG_TYPES: set[str] = {
    "min",
    "max",
    "count",
    "sum",
    "avg",
    "mean",
    "mode",
    "stddev",
    "variance",
    "median",
}

RECONCILE_OPERATION_NAME = "reconcile"
AGG_RECONCILE_OPERATION_NAME = "aggregates-reconcile"


class TableThresholdBoundsException(ValueError):
    """Raise the error when the bounds for table threshold are invalid"""


class InvalidModelForTableThreshold(ValueError):
    """Raise the error when the model for table threshold is invalid"""


@dataclass
class HashAlgoMapping:
    source: Callable
    target: Callable


@dataclass
class SamplingSpecifications:
    type: SamplingSpecificationsType
    value: float

    def __post_init__(self):
        if not isinstance(self.type, SamplingSpecificationsType):
            self.type = SamplingSpecificationsType(str(self.type).lower())
        # Disabled
        if self.type == SamplingSpecificationsType.FRACTION:
            raise ValueError("SamplingSpecifications: 'FRACTION' type is disabled")
        if self.type == SamplingSpecificationsType.FRACTION and (self.value is None or (not 0 < self.value < 1)):
            raise ValueError("SamplingSpecifications: Fraction value must be greater than  0 and less than 1")


@dataclass
class SamplingOptions:
    method: SamplingOptionMethod
    specifications: SamplingSpecifications
    stratified_columns: list[str] | None = None
    stratified_buckets: int | None = None

    def __post_init__(self):
        if not isinstance(self.method, SamplingOptionMethod):
            self.method = SamplingOptionMethod(str(self.method).lower())

        if self.stratified_columns:
            self.stratified_columns = [col.lower() for col in self.stratified_columns]

        if self.method == SamplingOptionMethod.STRATIFIED:
            if not self.stratified_columns or not self.stratified_buckets:
                raise ValueError(
                    "SamplingOptions : stratified_columns and stratified_buckets are required for STRATIFIED method"
                )


@dataclass
class JdbcReaderOptions:
    number_partitions: int | None = None
    partition_column: str | None = None
    lower_bound: str | None = None
    upper_bound: str | None = None
    fetch_size: int = 100

    def __post_init__(self):
        self.partition_column = self.partition_column.lower() if self.partition_column else None


@dataclass
class ColumnMapping:
    source_name: str
    target_name: str

    def __post_init__(self):
        self.source_name = self.source_name.lower()
        self.target_name = self.target_name.lower()


@dataclass
class Transformation:
    column_name: str
    source: str | None = None
    target: str | None = None

    def __post_init__(self):
        self.column_name = self.column_name.lower()


@dataclass
class ColumnThresholds:
    column_name: str
    lower_bound: str
    upper_bound: str
    type: str

    def __post_init__(self):
        self.column_name = self.column_name.lower()
        self.type = self.type.lower()

    def get_mode(self):
        return "percentage" if "%" in self.lower_bound or "%" in self.upper_bound else "absolute"

    def get_type(self):
        if any(self.type in numeric_type.value.lower() for numeric_type in exp.DataType.NUMERIC_TYPES):
            if self.get_mode() == "absolute":
                return "number_absolute"
            return "number_percentage"

        if any(self.type in numeric_type.value.lower() for numeric_type in exp.DataType.TEMPORAL_TYPES):
            return "datetime"
        return None


@dataclass
class TableThresholds:
    lower_bound: str
    upper_bound: str
    model: str

    def __post_init__(self):
        self.model = self.model.lower()
        self.validate_threshold_bounds()
        self.validate_threshold_model()

    def get_mode(self):
        return "percentage" if "%" in self.lower_bound or "%" in self.upper_bound else "absolute"

    def validate_threshold_bounds(self):
        lower_bound = int(self.lower_bound.replace("%", ""))
        upper_bound = int(self.upper_bound.replace("%", ""))
        if lower_bound < 0 or upper_bound < 0:
            raise TableThresholdBoundsException("Threshold bounds for table cannot be negative.")
        if lower_bound > upper_bound:
            raise TableThresholdBoundsException("Lower bound cannot be greater than upper bound.")

    def validate_threshold_model(self):
        if self.model not in ["mismatch"]:
            raise InvalidModelForTableThreshold(
                f"Invalid model for Table Threshold: expected 'mismatch', but got '{self.model}'."
            )


@dataclass
class Filters:
    source: str | None = None
    target: str | None = None


def to_lower_case(input_list: list[str]) -> list[str]:
    return [element.lower() for element in input_list]


@dataclass
class Table:
    source_name: str
    target_name: str
    sampling_options: SamplingOptions | None = None
    aggregates: list[Aggregate] | None = None
    join_columns: list[str] | None = None
    jdbc_reader_options: JdbcReaderOptions | None = None
    select_columns: list[str] | None = None
    drop_columns: list[str] | None = None
    column_mapping: list[ColumnMapping] | None = None
    transformations: list[Transformation] | None = None
    column_thresholds: list[ColumnThresholds] | None = None
    filters: Filters | None = None
    table_thresholds: list[TableThresholds] | None = None

    def __post_init__(self):
        self.source_name = self.source_name.lower()
        self.target_name = self.target_name.lower()
        self.select_columns = to_lower_case(self.select_columns) if self.select_columns else None
        self.drop_columns = to_lower_case(self.drop_columns) if self.drop_columns else None
        self.join_columns = to_lower_case(self.join_columns) if self.join_columns else None

    @property
    def to_src_col_map(self):
        if self.column_mapping:
            return {c.source_name: c.target_name for c in self.column_mapping}
        return None

    @property
    def to_tgt_col_map(self):
        if self.column_mapping:
            return {c.target_name: c.source_name for c in self.column_mapping}
        return None

    def get_src_to_tgt_col_mapping_list(self, cols: list[str], layer: str) -> set[str]:
        if layer == "source":
            return set(cols)
        if self.to_src_col_map:
            return {self.to_src_col_map.get(col, col) for col in cols}
        return set(cols)

    def get_layer_src_to_tgt_col_mapping(self, column_name: str, layer: str) -> str:
        if layer == "source":
            return column_name
        if self.to_src_col_map:
            return self.to_src_col_map.get(column_name, column_name)
        return column_name

    def get_tgt_to_src_col_mapping_list(self, cols: list[str] | set[str]) -> set[str]:
        if self.to_tgt_col_map:
            return {self.to_tgt_col_map.get(col, col) for col in cols}
        return set(cols)

    def get_layer_tgt_to_src_col_mapping(self, column_name: str, layer: str) -> str:
        if layer == "source":
            return column_name
        if self.to_tgt_col_map:
            return self.to_tgt_col_map.get(column_name, column_name)
        return column_name

    def get_select_columns(self, schema: list[Schema], layer: str) -> set[str]:
        if self.select_columns is None:
            return {sch.column_name for sch in schema}
        if self.to_src_col_map:
            return self.get_src_to_tgt_col_mapping_list(self.select_columns, layer)
        return set(self.select_columns)

    def get_threshold_columns(self, layer: str) -> set[str]:
        if self.column_thresholds is None:
            return set()
        return {self.get_layer_src_to_tgt_col_mapping(thresh.column_name, layer) for thresh in self.column_thresholds}

    def get_join_columns(self, layer: str) -> set[str] | None:
        if self.join_columns is None:
            return None
        return {self.get_layer_src_to_tgt_col_mapping(col, layer) for col in self.join_columns}

    def get_drop_columns(self, layer: str) -> set[str]:
        if self.drop_columns is None:
            return set()
        return {self.get_layer_src_to_tgt_col_mapping(col, layer) for col in self.drop_columns}

    def get_partition_column(self, layer: str) -> set[str]:
        if self.jdbc_reader_options and layer == "source":
            if self.jdbc_reader_options.partition_column:
                return {self.jdbc_reader_options.partition_column}
        return set()

    def get_filter(self, layer: str) -> str | None:
        if self.filters is None:
            return None
        if layer == "source":
            return self.filters.source
        return self.filters.target


@dataclass
class Schema:
    # TODO remove: This will have the value of ansi_normalized_column_name. Kept for backwards compatibility.
    column_name: str
    data_type: str
    ansi_normalized_column_name: str
    source_normalized_column_name: str


@dataclass
class Aggregate:
    agg_columns: list[str]
    type: str
    group_by_columns: list[str] | None = None

    def __post_init__(self):
        self.agg_columns = to_lower_case(self.agg_columns)
        self.type = self.type.lower()
        self.group_by_columns = to_lower_case(self.group_by_columns) if self.group_by_columns else None
        assert (
            self.type in _SUPPORTED_AGG_TYPES
        ), f"Invalid aggregate type: {self.type}, only {_SUPPORTED_AGG_TYPES} are supported."

    def get_agg_type(self):
        return self.type

    @classmethod
    def _join_columns(cls, columns: list[str]):
        return "+__+".join(columns)

    @property
    def group_by_columns_as_str(self):
        return self._join_columns(self.group_by_columns) if self.group_by_columns else "NA"

    @property
    def agg_columns_as_str(self):
        return self._join_columns(self.agg_columns)


@dataclass
class AggregateRule:
    agg_type: str
    agg_column: str
    group_by_columns: list[str] | None
    group_by_columns_as_str: str
    rule_type: str = "AGGREGATE"

    @property
    def column_from_rule(self):
        # creates rule_column. e.g., min_col1_grp1_grp2
        return f"{self.agg_type}_{self.agg_column}_{self.group_by_columns_as_str}"

    @property
    def group_by_columns_as_table_column(self):
        # If group_by_columns are not defined, store is as null
        group_by_cols_as_table_col = "NULL"
        if self.group_by_columns:
            # Sort the columns, convert to lower case and create a string:  , e.g., grp1, grp2
            formatted_cols = ", ".join([f"{col.lower()}" for col in sorted(self.group_by_columns)])
            group_by_cols_as_table_col = f"\"{formatted_cols}\""
        return group_by_cols_as_table_col

    def get_rule_query(self, rule_id):
        rule_info = f""" map( 'agg_type', '{self.agg_type}',
                 'agg_column', '{self.agg_column}',
                 'group_by_columns', {self.group_by_columns_as_table_column}
                 )
        """
        return f" SELECT {rule_id} as rule_id, " f" '{self.rule_type}' as rule_type, " f" {rule_info} as rule_info "


@dataclass
class AggregateQueryRules:
    layer: str
    group_by_columns: list[str] | None
    group_by_columns_as_str: str
    query: str
    rules: list[AggregateRule]
