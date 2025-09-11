import dataclasses

from databricks.labs.lakebridge.reconcile.connectors.data_source import DataSource
from databricks.labs.lakebridge.reconcile.recon_config import (
    Table,
    Aggregate,
    ColumnMapping,
    Transformation,
    ColumnThresholds,
)


class NormalizeReconConfigService:
    def __init__(self, source: DataSource, target: DataSource):
        self.source = source
        self.target = target

    def normalize_recon_table_config(self, table: Table) -> Table:
        normalized_table = dataclasses.replace(table)

        self._normalize_sampling(normalized_table)
        self._normalize_aggs(normalized_table)
        self._normalize_join_cols(normalized_table)
        self._normalize_select_cols(normalized_table)
        self._normalize_drop_cols(normalized_table)
        self._normalize_col_mappings(normalized_table)
        self._normalize_transformations(normalized_table)
        self._normalize_col_thresholds(normalized_table)
        self._normalize_jdbc_options(normalized_table)

        return normalized_table

    def _normalize_sampling(self, table: Table):
        if table.sampling_options:
            normalized_sampling = dataclasses.replace(table.sampling_options)
            normalized_sampling.stratified_columns = (
                [self.source.normalize_identifier(c).ansi_normalized for c in normalized_sampling.stratified_columns]
                if normalized_sampling.stratified_columns
                else None
            )
            table.sampling_options = normalized_sampling
        return table

    def _normalize_aggs(self, table: Table):
        normalized = [self._normalize_agg(a) for a in table.aggregates] if table.aggregates else None
        table.aggregates = normalized
        return table

    def _normalize_agg(self, agg: Aggregate) -> Aggregate:
        normalized = dataclasses.replace(agg)
        normalized.agg_columns = [self.source.normalize_identifier(c).ansi_normalized for c in normalized.agg_columns]
        normalized.group_by_columns = (
            [self.source.normalize_identifier(c).ansi_normalized for c in normalized.group_by_columns]
            if normalized.group_by_columns
            else None
        )
        return normalized

    def _normalize_join_cols(self, table: Table):
        table.join_columns = (
            [self.source.normalize_identifier(c).ansi_normalized for c in table.join_columns]
            if table.join_columns
            else None
        )
        return table

    def _normalize_select_cols(self, table: Table):
        table.select_columns = (
            [self.source.normalize_identifier(c).ansi_normalized for c in table.select_columns]
            if table.select_columns
            else None
        )
        return table

    def _normalize_drop_cols(self, table: Table):
        table.drop_columns = (
            [self.source.normalize_identifier(c).ansi_normalized for c in table.drop_columns]
            if table.drop_columns
            else None
        )
        return table

    def _normalize_col_mappings(self, table: Table):
        table.column_mapping = (
            [self._normalize_col_mapping(m) for m in table.column_mapping] if table.column_mapping else None
        )
        return table

    def _normalize_col_mapping(self, mapping: ColumnMapping):
        return ColumnMapping(
            source_name=self.source.normalize_identifier(mapping.source_name).ansi_normalized,
            target_name=self.target.normalize_identifier(mapping.target_name).ansi_normalized,
        )

    def _normalize_transformations(self, table: Table):
        table.transformations = (
            [self._normalize_transformation(t) for t in table.transformations] if table.transformations else None
        )
        return table

    def _normalize_transformation(self, transform: Transformation):
        """normalize user-configured transformations

        The user configures the table column and passes SQL code to transform the source table and target table.
        This is useful in scenarios when the data changes e.g. migrating `datetime`s. The SQL code is not normalized
        and it is the user responsibility to pass valid SQL respecting source database and target database.
        """
        normalized = dataclasses.replace(transform)
        normalized.column_name = self.source.normalize_identifier(transform.column_name).ansi_normalized
        return normalized

    def _normalize_col_thresholds(self, table: Table):
        table.column_thresholds = (
            [self._normalize_col_threshold(t) for t in table.column_thresholds] if table.column_thresholds else None
        )
        return table

    def _normalize_col_threshold(self, threshold: ColumnThresholds):
        normalized = dataclasses.replace(threshold)
        normalized.column_name = self.source.normalize_identifier(threshold.column_name).ansi_normalized
        return normalized

    def _normalize_jdbc_options(self, table: Table):
        if table.jdbc_reader_options:
            normalized = dataclasses.replace(table.jdbc_reader_options)
            normalized.partition_column = (
                self.source.normalize_identifier(normalized.partition_column).ansi_normalized
                if normalized.partition_column
                else None
            )
            table.jdbc_reader_options = normalized

        return table
