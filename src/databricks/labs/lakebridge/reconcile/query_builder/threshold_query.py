import logging

from sqlglot import expressions as exp
from sqlglot import select

from databricks.labs.lakebridge.reconcile.connectors.dialect_utils import DialectUtils
from databricks.labs.lakebridge.reconcile.query_builder.base import QueryBuilder
from databricks.labs.lakebridge.reconcile.query_builder.expression_generator import (
    anonymous,
    build_between,
    build_column,
    build_from_clause,
    build_if,
    build_join_clause,
    build_literal,
    build_sub,
    build_where_clause,
    coalesce,
)
from databricks.labs.lakebridge.reconcile.recon_config import ColumnThresholds
from databricks.labs.lakebridge.transpiler.sqlglot.generator.databricks import Databricks

logger = logging.getLogger(__name__)


class ThresholdQueryBuilder(QueryBuilder):
    # Comparison query
    def build_comparison_query(self) -> str:
        self._validate(
            self.table_conf.get_join_columns("source"), "Join Columns are compulsory for threshold comparison query"
        )
        join_columns = (
            self.table_conf.get_join_columns("source") if self.table_conf.get_join_columns("source") else set()
        )
        select_clause, where = self._generate_select_where_clause(join_columns)
        from_clause, join_clause = self._generate_from_and_join_clause(join_columns)
        # for threshold comparison query the dialect is always Databricks
        query = select(*select_clause).from_(from_clause).join(join_clause).where(where).sql(dialect=Databricks)
        logger.info(f"Threshold Comparison query: {query}")
        return query

    def _generate_select_where_clause(self, join_columns) -> tuple[list[exp.Expression], exp.Expression]:
        thresholds: list[ColumnThresholds] = (
            self.table_conf.column_thresholds if self.table_conf.column_thresholds else []
        )
        select_clause = []
        where_clause = []

        # threshold columns
        for threshold in thresholds:
            column = threshold.column_name
            base = exp.Paren(
                this=build_sub(
                    left_column_name=column,
                    left_table_name="source",
                    right_column_name=column,
                    right_table_name="databricks",
                    quoted=False,
                )
            ).transform(coalesce)

            select_exp, where = self._build_expression_type(threshold, base)
            select_clause.extend(select_exp)
            where_clause.append(where)
        # join columns
        for column in sorted(join_columns):
            select_clause.append(
                build_column(
                    this=column,
                    alias=f"{DialectUtils.unnormalize_identifier(column)}_source",
                    table_name="source",
                    quoted=True and self._is_add_quotes,
                )
            )
        where = build_where_clause(where_clause)

        return select_clause, where

    def _build_expression_alias_components(
        self,
        threshold: ColumnThresholds,
        base: exp.Expression,
    ) -> tuple[list[exp.Expression], exp.Expression]:
        select_clause = []
        column = threshold.column_name
        select_clause.append(
            build_column(
                this=column,
                alias=f"{DialectUtils.unnormalize_identifier(column)}_source",
                table_name="source",
                quoted=True and self._is_add_quotes,
            ).transform(coalesce)
        )
        select_clause.append(
            build_column(
                this=column,
                alias=f"{DialectUtils.unnormalize_identifier(column)}_databricks",
                table_name="databricks",
                quoted=True and self._is_add_quotes,
            ).transform(coalesce)
        )
        where_clause = exp.NEQ(this=base, expression=exp.Literal(this="0", is_string=False))
        return select_clause, where_clause

    def _build_expression_type(
        self,
        threshold: ColumnThresholds,
        base: exp.Expression,
    ) -> tuple[list[exp.Expression], exp.Expression]:
        column = threshold.column_name
        # default expressions
        select_clause, where_clause = self._build_expression_alias_components(threshold, base)

        if threshold.get_type() in {"number_absolute", "datetime"}:
            if threshold.get_type() == "datetime":
                # unix_timestamp expression only if it is datetime
                select_clause = [expression.transform(anonymous, "unix_timestamp({})") for expression in select_clause]
                base = base.transform(anonymous, "unix_timestamp({})")
                where_clause = exp.NEQ(this=base, expression=exp.Literal(this="0", is_string=False))

            # absolute threshold
            func = self._build_threshold_absolute_case
        elif threshold.get_type() == "number_percentage":
            # percentage threshold
            func = self._build_threshold_percentage_case
        else:
            error_message = f"Threshold type {threshold.get_type()} not supported for column {column}"
            logger.error(error_message)
            raise ValueError(error_message)

        select_clause.append(
            build_column(
                this=func(base=base, threshold=threshold),
                alias=f"{DialectUtils.unnormalize_identifier(column)}_match",
                quoted=True and self._is_add_quotes,
            )
        )

        return select_clause, where_clause

    def _generate_from_and_join_clause(self, join_columns) -> tuple[exp.From, exp.Join]:
        source_view = f"source_{self.table_conf.source_name}_df_threshold_vw"
        target_view = f"target_{self.table_conf.target_name}_df_threshold_vw"

        from_clause = build_from_clause(source_view, "source")
        join_clause = build_join_clause(
            table_name=target_view,
            source_table_alias="source",
            target_table_alias="databricks",
            join_columns=sorted(join_columns),
        )

        return from_clause, join_clause

    @classmethod
    def _build_threshold_absolute_case(
        cls,
        base: exp.Expression,
        threshold: ColumnThresholds,
    ) -> exp.Case:
        eq_if = build_if(
            this=exp.EQ(this=base, expression=build_literal(this="0", is_string=False)),
            true=exp.Literal(this="Match", is_string=True),
        )

        between_base = build_between(
            this=base,
            low=build_literal(threshold.lower_bound.replace("%", ""), is_string=False),
            high=build_literal(threshold.upper_bound.replace("%", ""), is_string=False),
        )

        between_if = build_if(
            this=between_base,
            true=exp.Literal(this="Warning", is_string=True),
        )
        return exp.Case(ifs=[eq_if, between_if], default=exp.Literal(this="Failed", is_string=True))

    @classmethod
    def _build_threshold_percentage_case(
        cls,
        base: exp.Expression,
        threshold: ColumnThresholds,
    ) -> exp.Case:
        eq_if = exp.If(
            this=exp.EQ(this=base, expression=build_literal(this="0", is_string=False)),
            true=exp.Literal(this="Match", is_string=True),
        )

        denominator = build_if(
            this=exp.Or(
                this=exp.EQ(
                    this=exp.Column(this=threshold.column_name, table="databricks"),
                    expression=exp.Literal(this='0', is_string=False),
                ),
                expression=exp.Is(
                    this=exp.Column(
                        this=threshold.column_name,
                        table="databricks",
                    ),
                    expression=exp.Null(),
                ),
            ),
            true=exp.Literal(this="1", is_string=False),
            false=exp.Column(this=threshold.column_name, table="databricks"),
        )

        division = exp.Div(this=base, expression=denominator, typed=False, safe=False)
        percentage = exp.Mul(this=exp.Paren(this=division), expression=exp.Literal(this="100", is_string=False))
        between_base = build_between(
            this=percentage,
            low=build_literal(threshold.lower_bound.replace("%", ""), is_string=False),
            high=build_literal(threshold.upper_bound.replace("%", ""), is_string=False),
        )

        between_if = build_if(
            this=between_base,
            true=exp.Literal(this="Warning", is_string=True),
        )
        return exp.Case(ifs=[eq_if, between_if], default=exp.Literal(this="Failed", is_string=True))

    def build_threshold_query(self) -> str:
        """
        This method builds a threshold query based on the configuration of the table and the columns involved.

        The query is constructed by selecting the necessary columns (partition, join, and threshold columns)
        from a specified table. Any transformations specified in the table configuration are applied to the
        selected columns. The query also includes a WHERE clause based on the filter defined in the table configuration.

        The resulting query is then converted to a SQL string using the dialect of the source database.

        Returns:
            str: The SQL string representation of the threshold query.
        """
        # key column expression
        self._validate(self.join_columns, "Join Columns are compulsory for threshold query")
        join_columns = self.join_columns if self.join_columns else set()
        keys: list[str] = sorted(self.partition_column.union(join_columns))
        keys_select_alias = [self._build_column_with_alias(col) for col in keys]
        keys_expr = self._apply_user_transformation(keys_select_alias)

        # threshold column expression
        threshold_alias = [self._build_column_with_alias(col) for col in sorted(self.threshold_columns)]
        thresholds_expr = threshold_alias
        if self.user_transformations:
            thresholds_expr = self._apply_user_transformation(threshold_alias)

        query = (select(*keys_expr + thresholds_expr).from_(":tbl").where(self.filter, dialect=self.engine)).sql(
            dialect=self.engine
        )
        logger.info(f"Threshold Query for {self.layer}: {query}")
        return query
