import logging

import sqlglot.expressions as exp
from pyspark.sql import DataFrame
from sqlglot import select

from databricks.labs.lakebridge.reconcile.connectors.dialect_utils import DialectUtils
from databricks.labs.lakebridge.transpiler.sqlglot.dialect_utils import get_key_from_dialect
from databricks.labs.lakebridge.reconcile.query_builder.base import QueryBuilder
from databricks.labs.lakebridge.reconcile.query_builder.expression_generator import (
    build_column,
    build_literal,
    _get_is_string,
    build_join_clause,
)

logger = logging.getLogger(__name__)


def _union_concat(
    unions: list[exp.Select],
    result: exp.Union | exp.Select,
    cnt=0,
) -> exp.Select | exp.Union:
    if len(unions) == 1:
        return result
    if cnt == len(unions) - 2:
        return exp.union(result, unions[cnt + 1])
    cnt = cnt + 1
    res = exp.union(result, unions[cnt])
    return _union_concat(unions, res, cnt)


class SamplingQueryBuilder(QueryBuilder):
    def build_query_with_alias(self):
        self._validate(self.join_columns, "Join Columns are compulsory for sampling query")
        join_columns = self.join_columns if self.join_columns else set()

        cols = sorted((join_columns | self.select_columns) - self.threshold_columns - self.drop_columns)

        cols_with_alias = [self._build_column_with_alias(col) for col in cols]

        query = select(*cols_with_alias).from_(":tbl").where(self.filter, dialect=self.engine).sql(dialect=self.engine)

        logger.info(f"Sampling Query with Alias for {self.layer}: {query}")
        return query

    def build_query(self, df: DataFrame):
        self._validate(self.join_columns, "Join Columns are compulsory for sampling query")
        join_columns = self.join_columns if self.join_columns else set()
        if self.layer == "source":
            key_cols = sorted(join_columns)
        else:
            key_cols = sorted(self.table_conf.get_tgt_to_src_col_mapping_list(join_columns))
        keys_df = df.select(*key_cols)
        with_clause = self._get_with_clause(keys_df)

        cols = sorted((join_columns | self.select_columns) - self.threshold_columns - self.drop_columns)

        cols_with_alias = [self._build_column_with_alias(col) for col in cols]

        sql_with_transforms = self.add_transformations(cols_with_alias, self.engine)
        query_sql = select(*sql_with_transforms).from_(":tbl").where(self.filter, dialect=self.engine)
        if self.layer == "source":
            with_select = [
                build_column(
                    this=DialectUtils.unnormalize_identifier(col), table_name="src", quoted=True and self._is_add_quotes
                )
                for col in sorted(cols)
            ]
        else:
            with_select = [
                build_column(
                    this=DialectUtils.unnormalize_identifier(col), table_name="src", quoted=True and self._is_add_quotes
                )
                for col in sorted(self.table_conf.get_tgt_to_src_col_mapping_list(cols))
            ]

        join_clause = self._get_join_clause(key_cols)

        query = (
            with_clause.with_(alias="src", as_=query_sql)
            .select(*with_select)
            .from_("src")
            .join(join_clause)
            .sql(dialect=self.engine)
        )
        logger.info(f"Sampling Query for {self.layer}: {query}")
        return query

    def _get_join_clause(self, key_cols: list):
        normalized = [self._build_column_name_source_normalized(col) for col in key_cols]
        return build_join_clause(
            "recon", normalized, source_table_alias="src", target_table_alias="recon", kind="inner", func=exp.EQ
        )

    def _get_with_clause(self, df: DataFrame) -> exp.Select:
        union_res = []
        for row in df.collect():
            column_types = [(str(f.name).lower(), f.dataType) for f in df.schema.fields]
            column_types_dict = dict(column_types)
            orig_types_dict = {
                schema.column_name: schema.data_type
                for schema in self.schema
                if schema.column_name not in self.user_transformations
            }
            row_select = [
                (
                    build_literal(
                        this=str(value),
                        alias=DialectUtils.unnormalize_identifier(col),
                        is_string=_get_is_string(column_types_dict, col),
                        cast=orig_types_dict.get(DialectUtils.ansi_normalize_identifier(col)),
                        quoted=True and self._is_add_quotes,
                    )
                    if value is not None
                    else exp.Alias(
                        this=exp.Null(),
                        alias=DialectUtils.unnormalize_identifier(col),
                        quoted=True and self._is_add_quotes,
                    )
                )
                for col, value in zip(df.columns, row)
            ]
            if get_key_from_dialect(self.engine) == "oracle":
                union_res.append(select(*row_select).from_("dual"))
            else:
                union_res.append(select(*row_select))
        union_statements = _union_concat(union_res, union_res[0], 0)
        return exp.Select().with_(alias='recon', as_=union_statements)
