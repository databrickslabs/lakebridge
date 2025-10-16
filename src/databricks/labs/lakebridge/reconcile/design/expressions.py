import dataclasses
import typing as t

import sqlglot.expressions as e
from sqlglot.dialects import Dialect as SqlglotDialect

DialectType = t.Union[str, SqlglotDialect, t.Type[SqlglotDialect], None]

@dataclasses.dataclass(frozen=True)
class ExpressionTransformation:
    func: t.Callable # isnt this Func
    args: dict


class ExpressionBuilder:
    _expression: e.Expression

    def __init__(self, column_name: str, dialect: str, table_name: str | None):
        self._column_name = column_name
        self._table_name = table_name
        self._dialect = dialect
        self._transformations: list[ExpressionTransformation] = []

    def build(self) -> str:
        if self._table_name:
            id_exp = e.Identifier(this=self.column_name, table=self._table_name)
        else:
            id_exp = e.Identifier(this=self.column_name)
        column = e.Column(this=id_exp)
        exp = self._apply_transformations(column)
        return exp.sql(dialect=self._dialect)

    def _apply_transformations(self, column: e.Column) -> e.Expression:
        exp = column
        for transformation in self._transformations:
            exp = transformation.func(exp.copy(), **transformation.args) # add error handling
        return exp

    def column_name(self, name: str):
        self._column_name = name
        return self

    def table_name(self, name: str):
        self._column_name = name
        return self

    def transform(self, func: t.Callable, **kwargs):
        transform = ExpressionTransformation(func, kwargs)
        self._transformations.append(transform)
        return self

def coalesce(column: ExpressionBuilder, default=0, is_string=False) -> ExpressionBuilder:
    expressions = [e.Literal(this=default, is_string=is_string)]
    return column.transform(e.Coalesce, expressions=expressions)

def trim(column: ExpressionBuilder) -> ExpressionBuilder:
    return column.transform(e.Trim)

def unix_time(column: ExpressionBuilder):
    return column.transform(e.TimeStrToUnix) #placeholder
