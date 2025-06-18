import logging
from itertools import groupby
from operator import attrgetter

import sqlglot.expressions as exp

from databricks.labs.lakebridge.reconcile.query_builder.base import QueryBuilder
from databricks.labs.lakebridge.reconcile.query_builder.expression_generator import (
    build_column,
)
from databricks.labs.lakebridge.reconcile.recon_config import (
    Aggregate,
    AggregateQueryRules,
    AggregateRule,
)

logger = logging.getLogger(__name__)


def _remove_aliases(node: exp.Expression) -> exp.Expression:
    if isinstance(node, exp.Alias):
        return node.this
    return node


class AggregateQueryBuilder(QueryBuilder):

    def _get_mapping_col(self, col: str) -> str:
        """
        Get the column mapping for the given column based on the layer

        Examples:
            Input :: col: "COL1",  mapping:  "{source: COL1, target: COLUMN1}", layer: "source"

            Returns -> "COLUMN1"

        :param col: Column Name
        :return: Mapped Column Name if found, else Column Name
        """
        # apply column mapping, ex: "{source: pid, target: product_id}"
        column_with_mapping = self.table_conf.get_layer_tgt_to_src_col_mapping(col, self.layer)
        if self.layer == "target":
            column_with_mapping = self.table_conf.get_layer_src_to_tgt_col_mapping(col, self.layer)
        return column_with_mapping

    def _get_mapping_cols_with_alias(self, cols_list: list[str], agg_type: str):
        """
        Creates a Column Expression for each [Mapped] Column with Agg_Type+Original_Column as Alias

        Examples:
            Input ::  cols_list: ["COL1", "COL2"], agg_type: ["MAX"] \n
            Returns -> ["column1 AS max<#>col1", "column2 AS max<#>col2]

        :param cols_list:  List of aggregate columns
        :param agg_type: MIN, MAX, COUNT, AVG
        :return: list[Expression] - List of Column Expressions with Alias
        """
        cols_with_mapping: list[exp.Expression] = []
        for col in cols_list:
            column_expr = build_column(
                this=f"{self._get_mapping_col(col)}", alias=f"{agg_type.lower()}<#>{col.lower()}"
            )
            cols_with_mapping.append(column_expr)
        return cols_with_mapping

    def _agg_query_cols_with_alias(self, transformed_cols: list[exp.Expression]):
        cols_with_alias = []

        for transformed_col in transformed_cols:
            # Split the alias defined above as agg_type(min, max etc..), original column (pid)
            agg_type, org_col_name = transformed_col.alias.split("<#>")

            # Create a new alias with layer, agg_type and original column name,
            # ex: source_min_pid, target_max_product_id
            layer_agg_type_col_alias = f"{self.layer}_{agg_type}_{org_col_name}".lower()

            # Get the Transformed column name without the alias
            col_name = transformed_col.sql().replace(f"AS {transformed_col.alias}", '').strip()

            # Create a new Column Expression with the new alias,
            # ex: MIN(pid) AS source_min_pid, MIN(product_id) AS target_min_pid
            column_name = f"{col_name}" if agg_type == "group_by" else f"{agg_type}({col_name})"
            col_with_alias = build_column(this=column_name, alias=layer_agg_type_col_alias)
            cols_with_alias.append(col_with_alias)

        return cols_with_alias

    def _get_layer_query(self, group_list: list[Aggregate]) -> AggregateQueryRules:
        """
        Builds the query based on the  layer:
        * Creates an Expression using
            - 'select' columns with alias for the aggregate columns
            - 'filters' (where) based on the layer
            - 'group by' if group_by_columns are defined
        * Generates and returns the SQL query using the above Expression and Dialect
        - query Aggregate rules

        Examples:
            1.Input :: group_list: [Aggregate(type="Max", agg_cols=["col2", "col3"], group_by_columns=["col1"]),
                              Aggregate(type="Sum", agg_cols=["col1", "col2"], group_by_columns=["col1"])]
                           Returns -> SELECT max(col2) AS src_max_col2, max(col3) AS src_max_col3,
                                                            sum(col1) AS src_sum_col1, sum(col2) AS src_sum_col2
                                                FROM :tbl
                                                WHERE col1 IS NOT NULL
                                                 GROUP BY col1
        2.
            group_list: [Aggregate(type="avg", agg_cols=["col4"])]
            :layer: "tgt"
            :returns -> SELECT avg(col4) AS tgt_avg_col4 FROM :tbl

        :param group_list: List of Aggregate objects with same Group by columns
        :return: str - SQL Query
        """
        cols_with_mapping: list[exp.Expression] = []
        # Generates a Single Query for multiple aggregates with the same group_by_columns,
        #   refer to Example 1
        query_agg_rules = []
        processed_rules: dict[str, str] = {}
        for agg in group_list:

            # Skip duplicate rules
            # Example: {min_grp1+__+grp2 : col1+__+col2}, key = min_grp1+__+grp2
            key = f"{agg.type}_{agg.group_by_columns_as_str}"
            if key in processed_rules:
                existing_rule = processed_rules.get(key)
                if existing_rule == agg.agg_columns_as_str:
                    logger.info(
                        f"Skipping duplicate rule for key: {key}, value: {agg.agg_columns_as_str},"
                        f" layer: {self.layer}"
                    )
                    continue
            processed_rules[key] = agg.agg_columns_as_str

            # Get the rules for each aggregate and append to the query_agg_rules list
            query_agg_rules.extend(self._build_aggregate_rules(agg))

            # Get the mapping with alias for aggregate columns and append to the cols_with_mapping list
            cols_with_mapping.extend(self._get_mapping_cols_with_alias(agg.agg_columns, agg.type))

        # Apply user transformations on Select columns
        # Example: {column_name: creation_date, source: creation_date, target: to_date(creation_date,'yyyy-mm-dd')}
        select_cols_with_transform = (
            self._apply_user_transformation(cols_with_mapping) if self.user_transformations else cols_with_mapping
        )

        # Transformed columns
        select_cols_with_alias = self._agg_query_cols_with_alias(select_cols_with_transform)
        query_exp = exp.select(*select_cols_with_alias).from_(":tbl").where(self.filter)

        assert group_list[0], "At least, one item must be present in the group_list."

        # Apply Group by if group_by_columns are defined
        if group_list[0].group_by_columns:
            group_by_cols_with_mapping = self._get_mapping_cols_with_alias(group_list[0].group_by_columns, "GROUP_BY")

            # Apply user transformations on group_by_columns,
            # ex: {column_name: creation_date, source: creation_date, target: to_date(creation_date,'yyyy-mm-dd')}
            group_by_cols_with_transform = (
                self._apply_user_transformation(group_by_cols_with_mapping)
                if self.user_transformations
                else group_by_cols_with_mapping
            )

            select_group_by_cols_with_alias = self._agg_query_cols_with_alias(group_by_cols_with_transform)

            # Group by column doesn't support alias (GROUP BY to_date(COL1, 'yyyy-MM-dd') AS col1) throws error
            group_by_col_without_alias = [
                build_column(this=_remove_aliases(group_by_col_with_alias).sql())
                for group_by_col_with_alias in select_group_by_cols_with_alias
                if " AS " in group_by_col_with_alias.sql()
            ]

            query_exp = (
                exp.select(*select_cols_with_alias + select_group_by_cols_with_alias)
                .from_(":tbl")
                .where(self.filter)
                .group_by(*group_by_col_without_alias)
            )

        agg_query_rules = AggregateQueryRules(
            layer=self.layer,
            group_by_columns=group_list[0].group_by_columns,
            group_by_columns_as_str=group_list[0].group_by_columns_as_str,
            query=query_exp.sql(dialect=self.engine),
            rules=query_agg_rules,
        )
        return agg_query_rules

    def grouped_aggregates(self):
        """
        Group items based on group_by_columns_keys:
        Example:
          aggregates = [
                                    Aggregate(type="Min", agg_cols=["c_nation_str", "col2"],
                                                                                    group_by_columns=["col3"]),
                                    Aggregate(type="Max", agg_cols=["col2", "col3"], group_by_columns=["col1"]),
                                    Aggregate(type="avg", agg_cols=["col4"]),
                                    Aggregate(type="sum", agg_cols=["col3", "col6"], group_by_columns=["col1"]),
                                ]
          output:
                  * key: NA with index 1
                    - Aggregate(agg_cols=['col4'], type='avg', group_by_columns=None, group_by_columns_as_str='NA')
                  * key: col1 with index 2
                    - Aggregate(agg_cols=['col2', 'col3'], type='Max', group_by_columns=['col1'],
                                                                                  group_by_columns_as_str='col1')
                    - Aggregate(agg_cols=['col3', 'col6'], type='sum', group_by_columns=['col1'],
                                                                                   group_by_columns_as_str='col1')
                  * key: col3 with index 3
                    - Aggregate(agg_cols=['c_nation_str', 'col2'], type='Min', group_by_columns=['col3'],
                    group_by_columns_as_str='col3')
        """
        _aggregates: list[Aggregate] = []

        assert self.aggregates, "Aggregates config must be defined to build the queries."
        self._validate(self.aggregates, "Aggregates config must be defined to build the queries.")

        if self.aggregates:
            _aggregates = self.aggregates

        # Sort the aggregates based on group_by_columns_as_str
        _aggregates.sort(key=attrgetter("group_by_columns_as_str"))

        return groupby(_aggregates, key=attrgetter("group_by_columns_as_str"))

    @classmethod
    def _build_aggregate_rules(cls, agg: Aggregate) -> list[AggregateRule]:
        """
        Builds the rules for each aggregate column in the given Aggregate object

        Example:
           Input :: Aggregate: {
                                              "type": "MIN",
                                              "agg_cols": ["COL1", "COL2"],
                                              "group_by_columns": ["GRP1", "GRP2]
                                            }
           Returns -> [AggregateRule(rule_id=hash(min_col1_grp1_grp2)),
                                                        query=SELECT {rule_id} as rule_id,
                                                        'min' as agg_type,
                                                        'col1' as agg_column,
                                                        ('grp1', 'grp2') as group_by_columns),

                                            AggregateRule(rule_id=hash(min_col2_grp1_grp2)),
                                                       query=SELECT {rule_id} as rule_id,
                                                        'min' as agg_type,
                                                        'col2' as agg_column,
                                                        ('grp1', 'grp2') as group_by_columns)]
        :param agg: Aggregate
        :return: list[AggregateRule]
        """

        return [
            AggregateRule(
                agg_type=agg.type,
                agg_column=agg_col,
                group_by_columns=agg.group_by_columns,
                group_by_columns_as_str=agg.group_by_columns_as_str,
            )
            for agg_col in agg.agg_columns
        ]

    def build_queries(self) -> list[AggregateQueryRules]:
        """
        Generates the Source and Target Queries for the list of Aggregate objects
        * Group items based on group_by_columns_keys and for each group,
            generates the query_with_rules for both Source and Target Dialects
        * Generates 2 Queries (Source, Target) for each unique group_by_columns_keys

        Examples:
        1. [Aggregate(type="avg", agg_cols=["col4"])]
            {
            "src_query_1": "SELECT avg(col4) AS src_avg_col4 FROM :tbl"
            }
            {
            "tgt_query_1": "SELECT avg(col4) AS tgt_avg_col4 FROM :tbl"
            }
         2. [Aggregate(type="Max", agg_cols=["col3"], group_by_columns=["col1"]),
              Aggregate(type="Sum", agg_cols=["col2"], group_by_columns=["col4"])]
            {
            "src_query_1": "SELECT max(col3) AS src_max_col3 FROM :tbl GROUP BY col1"
            "src_query_2": "SELECT sum(col2) AS src_sum_col2 FROM :tbl GROUP BY col4"
            }
            {
            "tgt_query_1": "SELECT max(col3) AS tgt_max_col3 FROM :tbl GROUP BY col1"
            "tgt_query_2": "SELECT sum(col2) AS tgt_sum_col2 FROM :tbl GROUP BY col4"
            }
        :return: Dictionary with Source and Target Queries
        """
        query_with_rules_list = []
        for key, group in self.grouped_aggregates():
            logger.info(f"Building Query and Rules for key: {key}, layer: {self.layer}")
            query_with_rules_list.append(self._get_layer_query(list(group)))

        return query_with_rules_list
