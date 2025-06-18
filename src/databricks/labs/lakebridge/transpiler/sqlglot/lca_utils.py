import logging
from collections.abc import Iterable
from pathlib import Path

from sqlglot import expressions as exp, Dialect
from sqlglot import parse
from sqlglot.errors import ErrorLevel, ParseError, TokenError, UnsupportedError
from sqlglot.expressions import Expression, Select
from sqlglot.optimizer.scope import Scope, build_scope

from databricks.labs.lakebridge.transpiler.transpile_status import TranspileError, ErrorKind, ErrorSeverity
from databricks.labs.lakebridge.transpiler.sqlglot.local_expression import AliasInfo

logger = logging.getLogger(__name__)


def check_for_unsupported_lca(
    from_dialect: Dialect,
    source_sql: str,
    file_path: Path,
) -> TranspileError | None:
    """
    Check for presence of unsupported lateral column aliases in window expressions and where clauses
    :return: An error if found
    """
    try:
        all_parsed_expressions: Iterable[Expression | None] = parse(
            source_sql, read=from_dialect, error_level=ErrorLevel.RAISE
        )
        root_expressions: Iterable[Expression] = [pe for pe in all_parsed_expressions if pe is not None]
    except (ParseError, TokenError, UnsupportedError) as e:
        logger.warning(f"Error while preprocessing {file_path}: {e}")
        return None

    aliases_in_where = set()
    aliases_in_window = set()

    for expr in root_expressions:
        for select in expr.find_all(exp.Select, bfs=False):
            alias_info = _find_aliases_in_select(select)
            aliases_in_where.update(_find_invalid_lca_in_where(select, alias_info))
            aliases_in_window.update(_find_invalid_lca_in_window(select, alias_info))

    if not (aliases_in_where or aliases_in_window):
        return None

    err_messages = [f"Unsupported operation found in file {file_path}. Needs manual review of transpiled query."]
    if aliases_in_where:
        err_messages.append(f"Lateral column aliases `{', '.join(aliases_in_where)}` found in where clause.")

    if aliases_in_window:
        err_messages.append(f"Lateral column aliases `{', '.join(aliases_in_window)}` found in window expressions.")

    return TranspileError("UNSUPPORTED_LCA", ErrorKind.ANALYSIS, ErrorSeverity.ERROR, file_path, " ".join(err_messages))


def unalias_lca_in_select(expr: exp.Expression) -> exp.Expression:
    if not isinstance(expr, exp.Select):
        return expr

    root_select: Scope | None = build_scope(expr)
    if not root_select:
        return expr

    # We won't search inside nested selects, they will be visited separately
    nested_selects = {*root_select.derived_tables, *root_select.subqueries}
    alias_info = _find_aliases_in_select(expr)
    where_ast: Expression | None = expr.args.get("where")
    if where_ast:
        for column in where_ast.walk(prune=lambda n: n in nested_selects):
            _replace_aliases(column, alias_info)
    for window in _find_windows_in_select(expr):
        for column in window.walk():
            _replace_aliases(column, alias_info)
    return expr


def _replace_aliases(column: Expression, alias_info: dict[str, AliasInfo]):
    if (
        isinstance(column, exp.Column)
        and column.name in alias_info
        and not alias_info[column.name].is_same_name_as_column
    ):
        unaliased_expr = alias_info[column.name].expression
        column.replace(unaliased_expr)
        for col in unaliased_expr.walk():
            _replace_aliases(col, alias_info)


def _find_windows_in_select(select: Select) -> list[exp.Window]:
    window_expressions = []
    for expr in select.expressions:
        window_expr = expr.find(exp.Window)
        if window_expr:
            window_expressions.append(window_expr)
    return window_expressions


def _find_aliases_in_select(select_expr: Select) -> dict[str, AliasInfo]:
    aliases = {}
    for expr in select_expr.expressions:
        if isinstance(expr, exp.Alias):
            alias_name = expr.output_name
            is_same_name_as_column = False
            for column in expr.find_all(exp.Column):
                if column.name == alias_name:
                    is_same_name_as_column = True
                    break
            aliases[alias_name] = AliasInfo(alias_name, expr.unalias().copy(), is_same_name_as_column)
    return aliases


def _find_invalid_lca_in_where(
    select_expr: Select,
    aliases: dict[str, AliasInfo],
) -> set[str]:
    aliases_in_where = set()
    where_ast: Expression | None = select_expr.args.get("where")
    if where_ast:
        for column in where_ast.find_all(exp.Column):
            if column.name in aliases and not aliases[column.name].is_same_name_as_column:
                aliases_in_where.add(column.name)

    return aliases_in_where


def _find_invalid_lca_in_window(
    select_expr: Select,
    aliases: dict[str, AliasInfo],
) -> set[str]:
    aliases_in_window = set()
    windows = _find_windows_in_select(select_expr)
    for window in windows:
        for column in window.find_all(exp.Column):
            if column.name in aliases and not aliases[column.name].is_same_name_as_column:
                aliases_in_window.add(column.name)

    return aliases_in_window
