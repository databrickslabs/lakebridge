import dataclasses
import typing as t
from abc import ABC, abstractmethod
from functools import reduce

import sqlglot.expressions as e
from duckdb.duckdb import alias
from sqlglot.dialects import Dialect as SqlglotDialect

DialectType = t.Union[str, SqlglotDialect, t.Type[SqlglotDialect], None]

@dataclasses.dataclass(frozen=True)
class ExpressionTransformation:
    func: t.Callable # isnt this Func
    args: dict


class AnyExpression(ABC):
    @abstractmethod
    def build(self) -> str:
        pass


class ExpressionBuilder(AnyExpression):
    _expression: e.Expression

    def __init__(self, column_name: str, dialect: str, table_name: str | None = None):
        self._column_name = column_name
        self._alias = None
        self._table_name = table_name
        self._dialect = dialect
        self._transformations: list[ExpressionTransformation] = []

    def build(self) -> str:
        if self._table_name:
            column = e.Column(this=self._column_name, table=self._table_name)
        else:
            column = e.Column(this=self._column_name, quoted=False)
        aliased = e.Alias(this=column, alias=self._alias) if self._alias else column
        transformed = self._apply_transformations(aliased)
        select_stmt = e.select(transformed).sql(dialect=self._dialect)
        return select_stmt.removeprefix("SELECT ") # return only column with the transformations

    def _apply_transformations(self, column: e.Expression) -> e.Expression:
        exp = column
        for transformation in self._transformations:
            exp = transformation.func(this=exp.copy(), **transformation.args) # add error handling
        return exp

    def column_name(self, name: str):
        self._column_name = name
        return self

    def alias(self, alias: str | None):
        self._alias = alias
        return self

    def table_name(self, name: str):
        self._column_name = name
        return self

    def transform(self, func: t.Callable, **kwargs):
        transform = ExpressionTransformation(func, kwargs)
        self._transformations.append(transform)
        return self

    def concat(self, other: "ExpressionBuilder"):
        pass

class HashExpressionsBuilder(AnyExpression):

    def __init__(self, dialect: str, columns: list[ExpressionBuilder]):
        self._dialect = dialect
        self._alias = None
        self._expressions: list[ExpressionBuilder] = columns

    def build(self) -> str:
        columns_to_hash = [col.alias(None).build() for col in self._expressions]
        columns_to_hash_expr = [e.Column(this=col) for col in columns_to_hash]
        concat_expr = e.Concat(expressions=columns_to_hash_expr)
        if self._dialect == "oracle":
            concat_expr = reduce(lambda x, y: e.DPipe(this=x, expression=y), concat_expr.expressions)
        match self._dialect: # Implement for the rest
            case "tsql": return (
                "CONVERT(VARCHAR(256), HASHBYTES("
                "'SHA2_256', CONVERT(VARCHAR(256),{})), 2)"
                f" AS {self._alias}" if self._alias else ""
                .format(concat_expr.sql(dialect=self._dialect))
            )
            case _:
                sha = e.SHA2(this=concat_expr, length=e.Literal(this=256, is_string=False))
                if self._alias: sha = e.Alias(this=sha, alias=self._alias)
                return sha.sql(dialect=self._dialect)

    def alias(self, alias: str | None):
        self._alias = alias
        return self


class QueryBuilder:

    def __init__(self, dialect: str, columns: list[AnyExpression]):
        self._dialect = dialect
        self._expressions: list[AnyExpression] = columns

    def build(self) -> str:
        select = [ex.build() for ex in self._expressions]
        return e.select(*select).from_(":table").sql(dialect=self._dialect)


def coalesce(column: ExpressionBuilder, default=0, is_string=False) -> ExpressionBuilder:
    expressions = [e.Literal(this=default, is_string=is_string)]
    return column.transform(e.Coalesce, expressions=expressions)

def trim(column: ExpressionBuilder) -> ExpressionBuilder:
    return column.transform(e.Trim)

def unix_time(column: ExpressionBuilder):
    return column.transform(e.TimeStrToUnix) #placeholder

