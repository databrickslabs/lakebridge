import logging
import re

from sqlglot import expressions as exp
from sqlglot.dialects.databricks import Databricks as SqlglotDatabricks
from sqlglot.dialects.hive import Hive
from sqlglot.dialects.dialect import if_sql
from sqlglot.dialects.dialect import rename_func
from sqlglot.errors import UnsupportedError
from sqlglot.helper import apply_index_offset, csv

from databricks.labs.lakebridge.transpiler.sqlglot import local_expression
from databricks.labs.lakebridge.transpiler.sqlglot.lca_utils import unalias_lca_in_select

# pylint: disable=too-many-public-methods

logger = logging.getLogger(__name__)

VALID_DATABRICKS_TYPES = {
    "BIGINT",
    "BINARY",
    "BOOLEAN",
    "DATE",
    "DECIMAL",
    "DOUBLE",
    "FLOAT",
    "INT",
    "INTERVAL",
    "VOID",
    "SMALLINT",
    "STRING",
    "TIMESTAMP",
    "TINYINT",
    "ARRAY",
    "MAP",
    "STRUCT",
}

PRECISION_CONST = 38
SCALE_CONST = 0


def timestamptrunc_sql(self, expression: exp.TimestampTrunc) -> str:
    return self.func("DATE_TRUNC", exp.Literal.string(expression.text("unit").upper()), self.sql(expression.this))


def _parm_sfx(self, expression: local_expression.Parameter) -> str:
    this = self.sql(expression, "this")
    this = f"{{{this}}}" if expression.args.get("wrapped") else f"{this}"
    suffix = self.sql(expression, "suffix")
    PARAMETER_TOKEN = "$"  # noqa: N806 pylint: disable=invalid-name
    return f"{PARAMETER_TOKEN}{this}{suffix}"


def _lateral_bracket_sql(self, expression: local_expression.Bracket) -> str:
    """Overwrites `sqlglot/generator.py` `bracket_sql()` function
    to convert <TABLE_ALIAS>`[COL_NAME]` to <TABLE_ALIAS>`.COL_NAME`.
    Example: c[val] ==> c.val
    """
    expressions = apply_index_offset(expression.this, expression.expressions, self.dialect.INDEX_OFFSET)
    expressions = [self.sql(e.alias_or_name.strip("'")) for e in expressions]
    # If expression contains space in between encode it in backticks(``):
    # e.g. ref."ID Number" -> ref.`ID Number`.
    expressions_sql = ", ".join(f"`{e}`" if " " in e else e for e in expressions)
    return f"{self.sql(expression, 'this')}:{expressions_sql}"


def _format_create_sql(self, expression: exp.Create) -> str:
    expression = expression.copy()

    # Remove modifiers in order to simplify the schema.  For example, this removes things like "IF NOT EXISTS"
    # from "CREATE TABLE foo IF NOT EXISTS".
    args_to_delete = ["temporary", "transient", "external", "exists", "unique", "materialized", "properties"]
    for arg_to_delete in args_to_delete:
        if expression.args.get(arg_to_delete):
            del expression.args[arg_to_delete]

    return self.create_sql(expression)


def _curr_time():
    return "date_format(current_timestamp(), 'HH:mm:ss')"


def _select_contains_index(expression: exp.Select) -> bool:
    for expr in expression.expressions:
        column = expr.unalias() if isinstance(expr, exp.Alias) else expr
        if column.name == "index":
            return True
    return False


def _has_parse_json(expression):
    if expression.find(exp.ParseJSON):
        return True
    _select = expression.find_ancestor(exp.Select)
    if _select:
        _from = _select.find(exp.From)
        if _from:
            _parse_json = _from.find(exp.ParseJSON)
            if _parse_json:
                return True
    return False


def _generate_function_str(select_contains_index, has_parse_json, generator_expr, alias, is_outer, alias_str):
    if select_contains_index:
        generator_function_str = f"POSEXPLODE({generator_expr})"
        alias_str = f"{' ' + alias.name if isinstance(alias, exp.TableAlias) else ''} AS index, value"
    elif has_parse_json and is_outer:
        generator_function_str = f"VARIANT_EXPLODE_OUTER({generator_expr})"
    elif has_parse_json:
        generator_function_str = f"VARIANT_EXPLODE({generator_expr})"
    else:
        generator_function_str = f"VIEW EXPLODE({generator_expr})"

    return generator_function_str, alias_str


def _generate_lateral_statement(self, select_contains_index, has_parse_json, generator_function_str, alias_str):
    if select_contains_index:
        lateral_statement = self.sql(f"LATERAL VIEW OUTER {generator_function_str}{alias_str}")
    elif has_parse_json:
        lateral_statement = self.sql(f", LATERAL {generator_function_str}{alias_str}")
    else:
        lateral_statement = self.sql(f" LATERAL {generator_function_str}{alias_str}")

    return lateral_statement


def _lateral_view(self: SqlglotDatabricks.Generator, expression: exp.Lateral) -> str:
    has_parse_json = _has_parse_json(expression)
    this = expression.args['this']
    alias = expression.args['alias']
    alias_str = f" AS {alias.name}" if isinstance(alias, exp.TableAlias) else ""
    generator_function_str = self.sql(this)
    is_outer = False
    select_contains_index = False

    if isinstance(this, exp.Explode):
        explode_expr = this
        parent_select = explode_expr.parent_select
        select_contains_index = _select_contains_index(parent_select) if parent_select else False
        generator_expr = ""
        if isinstance(explode_expr.this, exp.Kwarg):
            generator_expr = self.sql(explode_expr.this, 'expression')
            if not isinstance(explode_expr.this.expression, exp.ParseJSON):
                generator_expr = generator_expr.replace("{", "").replace("}", "")
        for expr in explode_expr.expressions:
            node = str(expr.this).upper()
            if node == "PATH":
                generator_expr += "." + self.sql(expr, 'expression').replace("'", "")
            if node == "OUTER":
                is_outer = True

        if not generator_expr:
            generator_expr = expression.this.this

        generator_function_str, alias_str = _generate_function_str(
            select_contains_index, has_parse_json, generator_expr, alias, is_outer, alias_str
        )

    alias_cols = alias.columns if alias else []
    if len(alias_cols) <= 2:
        alias_str = f" As {', '.join([item.this for item in alias_cols])}"

    lateral_statement = _generate_lateral_statement(
        self, select_contains_index, has_parse_json, generator_function_str, alias_str
    )
    return lateral_statement


# [TODO] Add more datatype coverage https://docs.databricks.com/sql/language-manual/sql-ref-datatypes.html
def _datatype_map(self, expression) -> str:
    if expression.this in [exp.DataType.Type.VARCHAR, exp.DataType.Type.NVARCHAR, exp.DataType.Type.CHAR]:
        return "STRING"
    if expression.this in [exp.DataType.Type.TIMESTAMP, exp.DataType.Type.TIMESTAMPLTZ]:
        return "TIMESTAMP"
    if expression.this == exp.DataType.Type.BINARY:
        return "BINARY"
    if expression.this == exp.DataType.Type.NCHAR:
        return "STRING"
    return self.datatype_sql(expression)


def try_to_date(self, expression: local_expression.TryToDate):
    func = "TRY_TO_TIMESTAMP"
    time_format = self.sql(expression, "format")
    if not time_format:
        time_format = Hive.DATE_FORMAT

    ts_result = self.func(func, expression.this, time_format)
    return exp.Date(this=ts_result)


def try_to_number(self, expression: local_expression.TryToNumber):
    func = "TRY_TO_NUMBER"
    precision = self.sql(expression, "precision")
    scale = self.sql(expression, "scale")

    if not precision:
        precision = 38

    if not scale:
        scale = 0

    func_expr = self.func(func, expression.this)
    if expression.expression:
        func_expr = self.func(func, expression.this, expression.expression)
    else:
        func_expr = expression.this

    return f"CAST({func_expr} AS DECIMAL({precision}, {scale}))"


def _to_boolean(self: SqlglotDatabricks.Generator, expression: local_expression.ToBoolean) -> str:
    this = self.sql(expression, "this")
    logger.debug(f"Converting {this} to Boolean")
    raise_error = self.sql(expression, "raise_error")
    raise_error_str = "RAISE_ERROR('Invalid parameter type for TO_BOOLEAN')" if bool(int(raise_error)) else "NULL"
    transformed = f"""
    CASE
       WHEN {this} IS NULL THEN NULL
       WHEN TYPEOF({this}) = 'boolean' THEN BOOLEAN({this})
       WHEN TYPEOF({this}) = 'string' THEN
           CASE
               WHEN LOWER({this}) IN ('true', 't', 'yes', 'y', 'on', '1') THEN TRUE
               WHEN LOWER({this}) IN ('false', 'f', 'no', 'n', 'off', '0') THEN FALSE
               ELSE RAISE_ERROR('Boolean value of x is not recognized by TO_BOOLEAN')
               END
       WHEN TRY_CAST({this} AS DOUBLE) IS NOT NULL THEN
           CASE
               WHEN ISNAN(CAST({this} AS DOUBLE)) OR CAST({this} AS DOUBLE) = DOUBLE('infinity') THEN
                    RAISE_ERROR('Invalid parameter type for TO_BOOLEAN')
               ELSE CAST({this} AS DOUBLE) != 0.0
               END
       ELSE {raise_error_str}
       END
    """
    return transformed


def _is_integer(self: SqlglotDatabricks.Generator, expression: local_expression.IsInteger) -> str:
    this = self.sql(expression, "this")
    transformed = f"""
    CASE
       WHEN {this} IS NULL THEN NULL
       WHEN {this} RLIKE '^-?[0-9]+$' AND TRY_CAST({this} AS INT) IS NOT NULL THEN TRUE
       ELSE FALSE
       END
    """
    return transformed


def _parse_json_extract_path_text(
    self: SqlglotDatabricks.Generator, expression: local_expression.JsonExtractPathText
) -> str:
    this = self.sql(expression, "this")
    path_name = expression.args["path_name"]
    if path_name.is_string:
        path = f"{self.dialect.QUOTE_START}$.{expression.text('path_name')}{self.dialect.QUOTE_END}"
    else:
        path = f"CONCAT('$.', {self.sql(expression, 'path_name')})"
    return f"GET_JSON_OBJECT({this}, {path})"


def _array_construct_compact(
    self: SqlglotDatabricks.Generator, expression: local_expression.ArrayConstructCompact
) -> str:
    exclude = "ARRAY(NULL)"
    array_expr = f"ARRAY({self.expressions(expression, flat=True)})"
    return f"ARRAY_EXCEPT({array_expr}, {exclude})"


def _array_slice(self: SqlglotDatabricks.Generator, expression: local_expression.ArraySlice) -> str:
    from_expr = self.sql(expression, "from")
    # In Databricks: array indices start at 1 in function `slice(array, start, length)`
    parsed_from_expr = 1 if from_expr == "0" else from_expr

    to_expr = self.sql(expression, "to")
    # Convert string expression to number and check if it is negative number
    if int(to_expr) < 0:
        err_message = "In Databricks: function `slice` length must be greater than or equal to 0"
        raise UnsupportedError(err_message)

    func = "SLICE"
    func_expr = self.func(func, expression.this, exp.Literal.number(parsed_from_expr), expression.args["to"])
    return func_expr


def _to_command(self, expr: exp.Command):
    this_sql = self.sql(expr, 'this')
    expression = self.sql(expr.expression, 'this')
    prefix = f"-- {this_sql}"
    if this_sql == "!":
        return f"{prefix}{expression}"
    return f"{prefix} {expression}"


def _parse_json(self, expression: exp.ParseJSON) -> str:
    return self.func("PARSE_JSON", expression.this, expression.expression)


def _to_number(self, expression: local_expression.ToNumber):
    func = "TO_NUMBER"
    precision = self.sql(expression, "precision")
    scale = self.sql(expression, "scale")

    func_expr = expression.this
    # if format is provided, else it will be vanilla cast to decimal
    if expression.expression:
        func_expr = self.func(func, expression.this, expression.expression)
        if precision:
            return f"CAST({func_expr} AS DECIMAL({precision}, {scale}))"
        return func_expr
    if not precision:
        precision = 38
    if not scale:
        scale = 0
    if not expression.expression and not precision:
        exception_msg = f"""Error Parsing expression {expression}:
                         * `format`: is required in Databricks [mandatory]
                         * `precision` and `scale`: are considered as (38, 0) if not specified.
                      """
        raise UnsupportedError(exception_msg)

    precision = PRECISION_CONST if not precision else precision
    scale = SCALE_CONST if not scale else scale
    return f"CAST({func_expr} AS DECIMAL({precision}, {scale}))"


def _uuid(self: SqlglotDatabricks.Generator, expression: local_expression.UUID) -> str:
    namespace = self.sql(expression, "this")
    name = self.sql(expression, "name")

    if namespace and name:
        logger.warning("UUID version 5 is not supported currently. Needs manual intervention.")
        return f"UUID({namespace}, {name})"

    return "UUID()"


def _parse_date_trunc(self: SqlglotDatabricks.Generator, expression: local_expression.DateTrunc) -> str:
    if not expression.args.get("unit"):
        error_message = f"Required keyword: 'unit' missing for {exp.DateTrunc}"
        raise UnsupportedError(error_message)
    return self.func("TRUNC", expression.this, expression.args.get("unit"))


def _get_within_group_params(
    expr: exp.ArrayAgg | exp.GroupConcat,
    within_group: exp.WithinGroup,
) -> local_expression.WithinGroupParams:
    has_distinct = isinstance(expr.this, exp.Distinct)
    agg_col = expr.this.expressions[0] if has_distinct else expr.this
    order_clause = within_group.expression
    order_cols = []
    for e in order_clause.expressions:
        desc = e.args.get("desc")
        is_order_a = not desc or exp.false() == desc
        order_cols.append((e.this, is_order_a))
    return local_expression.WithinGroupParams(
        agg_col=agg_col,
        order_cols=order_cols,
    )


def _create_named_struct_for_cmp(wg_params: local_expression.WithinGroupParams) -> exp.Expression:
    agg_col = wg_params.agg_col
    order_kv = []
    for i, (col, _) in enumerate(wg_params.order_cols):
        order_kv.extend([exp.Literal(this=f"sort_by_{i}", is_string=True), col])

    named_struct_func = exp.Anonymous(
        this="named_struct",
        expressions=[
            exp.Literal(this="value", is_string=True),
            agg_col,
            *order_kv,
        ],
    )
    return named_struct_func


def _current_date(self, expression: exp.CurrentDate) -> str:
    zone = self.sql(expression, "this")
    return f"CURRENT_DATE({zone})" if zone else "CURRENT_DATE()"


def _not_sql(self, expression: exp.Not) -> str:
    if isinstance(expression.this, exp.Is):
        return f"{self.sql(expression.this, 'this')} IS NOT {self.sql(expression.this, 'expression')}"
    return f"NOT {self.sql(expression, 'this')}"


def to_array(self, expression: exp.ToArray) -> str:
    return f"IF({self.sql(expression.this)} IS NULL, NULL, {self.func('ARRAY', expression.this)})"


class Databricks(SqlglotDatabricks):  #
    # Instantiate Databricks Dialect
    databricks = SqlglotDatabricks()
    NULL_ORDERING = "nulls_are_small"

    class Generator(SqlglotDatabricks.Generator):
        INVERSE_TIME_MAPPING: dict[str, str] = {
            **{v: k for k, v in SqlglotDatabricks.TIME_MAPPING.items()},
            "%-d": "dd",
        }

        COLLATE_IS_FUNC = True
        # [TODO]: Variant needs to be transformed better, for now parsing to string was deemed as the choice.
        TYPE_MAPPING = {
            **SqlglotDatabricks.Generator.TYPE_MAPPING,
            exp.DataType.Type.TINYINT: "TINYINT",
            exp.DataType.Type.SMALLINT: "SMALLINT",
            exp.DataType.Type.INT: "INT",
            exp.DataType.Type.BIGINT: "BIGINT",
            exp.DataType.Type.DATETIME: "TIMESTAMP",
            exp.DataType.Type.VARCHAR: "STRING",
            exp.DataType.Type.VARIANT: "VARIANT",
            exp.DataType.Type.FLOAT: "DOUBLE",
            exp.DataType.Type.OBJECT: "STRING",
            exp.DataType.Type.GEOGRAPHY: "STRING",
        }

        TRANSFORMS = {
            **SqlglotDatabricks.Generator.TRANSFORMS,
            exp.Create: _format_create_sql,
            exp.DataType: _datatype_map,
            exp.CurrentTime: _curr_time(),
            exp.Lateral: _lateral_view,
            exp.FromBase64: rename_func("UNBASE64"),
            exp.AutoIncrementColumnConstraint: lambda *_: "GENERATED ALWAYS AS IDENTITY",
            local_expression.Parameter: _parm_sfx,
            local_expression.ToBoolean: _to_boolean,
            local_expression.Bracket: _lateral_bracket_sql,
            local_expression.MakeDate: rename_func("MAKE_DATE"),
            local_expression.TryToDate: try_to_date,
            local_expression.TryToNumber: try_to_number,
            local_expression.IsInteger: _is_integer,
            local_expression.JsonExtractPathText: _parse_json_extract_path_text,
            local_expression.BitOr: rename_func("BIT_OR"),
            local_expression.ArrayConstructCompact: _array_construct_compact,
            local_expression.ArrayIntersection: rename_func("ARRAY_INTERSECT"),
            local_expression.ArraySlice: _array_slice,
            local_expression.ObjectKeys: rename_func("JSON_OBJECT_KEYS"),
            exp.ParseJSON: _parse_json,
            local_expression.TimestampFromParts: rename_func("MAKE_TIMESTAMP"),
            local_expression.ToDouble: rename_func("DOUBLE"),
            exp.Rand: rename_func("RANDOM"),
            local_expression.ToVariant: rename_func("TO_JSON"),
            local_expression.ToObject: rename_func("TO_JSON"),
            exp.ToBase64: rename_func("BASE64"),
            local_expression.ToNumber: _to_number,
            local_expression.UUID: _uuid,
            local_expression.DateTrunc: _parse_date_trunc,
            exp.ApproxQuantile: rename_func("APPROX_PERCENTILE"),
            exp.TimestampTrunc: timestamptrunc_sql,
            exp.Mod: rename_func("MOD"),
            exp.NullSafeEQ: lambda self, e: self.binary(e, "<=>"),
            exp.If: if_sql(false_value="NULL"),
            exp.Command: _to_command,
            exp.CurrentDate: _current_date,
            exp.Not: _not_sql,
            local_expression.ToArray: to_array,
            local_expression.ArrayExists: rename_func("EXISTS"),
        }

        def preprocess(self, expression: exp.Expression) -> exp.Expression:
            fixed_ast = expression.transform(unalias_lca_in_select, copy=False)
            return super().preprocess(fixed_ast)

        def format_time(self, expression: exp.Expression, inverse_time_mapping=None, inverse_time_trie=None):
            return super().format_time(expression, self.INVERSE_TIME_MAPPING)

        def join_sql(self, expression: exp.Join) -> str:
            """Overwrites `join_sql()` in `sqlglot/generator.py`
            Added logic to handle Lateral View
            """
            op_list = [
                expression.method,
                "GLOBAL" if expression.args.get("global") else None,
                expression.side,
                expression.kind,
                expression.hint if self.JOIN_HINTS else None,
            ]

            op_sql = " ".join(op for op in op_list if op)
            on_sql = self.sql(expression, "on")
            using = expression.args.get("using")

            if not on_sql and using:
                on_sql = csv(*(self.sql(column) for column in using))

            this_sql = self.sql(expression, "this")

            if on_sql:
                on_sql = self.indent(on_sql, skip_first=True)
                space = self.seg(" " * self.pad) if self.pretty else " "
                if using:
                    on_sql = f"{space}USING ({on_sql})"
                else:
                    on_sql = f"{space}ON {on_sql}"
            # Added the below elif block to handle Lateral View clause
            elif not op_sql and isinstance(expression.this, exp.Lateral):
                return f"\n {this_sql}"
            elif not op_sql:
                return f", {this_sql}"

            op_sql = f"{op_sql} JOIN" if op_sql else "JOIN"
            return f"{self.seg(op_sql)} {this_sql}{on_sql}"

        def arrayagg_sql(self, expression: exp.ArrayAgg) -> str:
            sql = self.func("ARRAY_AGG", expression.this)
            within_group = expression.parent if isinstance(expression.parent, exp.WithinGroup) else None
            if not within_group:
                return sql

            wg_params = _get_within_group_params(expression, within_group)
            if len(wg_params.order_cols) == 1:
                order_col, is_order_asc = wg_params.order_cols[0]
                if wg_params.agg_col == order_col:
                    return f"SORT_ARRAY({sql}{'' if is_order_asc else ', FALSE'})"

            named_struct_func = _create_named_struct_for_cmp(wg_params)
            comparisons = []
            for i, (_, is_order_asc) in enumerate(wg_params.order_cols):
                comparisons.append(
                    f"WHEN left.sort_by_{i} < right.sort_by_{i} THEN {'-1' if is_order_asc else '1'} "
                    f"WHEN left.sort_by_{i} > right.sort_by_{i} THEN {'1' if is_order_asc else '-1'}"
                )

            array_sort = self.func(
                "ARRAY_SORT",
                self.func("ARRAY_AGG", named_struct_func),
                f"""(left, right) -> CASE
                        {' '.join(comparisons)}
                        ELSE 0
                    END""",
            )
            return self.func("TRANSFORM", array_sort, "s -> s.value")

        def groupconcat_sql(self, expr: exp.GroupConcat) -> str:
            arr_agg = exp.ArrayAgg(this=expr.this)
            within_group = expr.parent.copy() if isinstance(expr.parent, exp.WithinGroup) else None
            if within_group:
                arr_agg.parent = within_group

            return self.func(
                "ARRAY_JOIN",
                arr_agg,
                expr.args.get("separator") or exp.Literal(this="", is_string=True),
            )

        def withingroup_sql(self, expression: exp.WithinGroup) -> str:
            agg_expr = expression.this
            if isinstance(agg_expr, (exp.ArrayAgg, exp.GroupConcat)):
                return self.sql(agg_expr)

            return super().withingroup_sql(expression)

        def split_sql(self, expression: local_expression.Split) -> str:
            """
            :param expression: local_expression.Split expression to be parsed
            :return: Converted expression (SPLIT) compatible with Databricks
            """
            delimiter = " "
            # To handle default delimiter
            if expression.expression:
                delimiter = expression.expression.name

            # Parsing logic to handle String and Table columns
            if expression.name and isinstance(expression.name, str):
                expr_name = f"'{expression.name}'"
            else:
                expr_name = expression.args["this"]
            return f"""SPLIT({expr_name},'[{delimiter}]')"""

        def delete_sql(self, expression: exp.Delete) -> str:
            this = self.sql(expression, "this")
            using = self.sql(expression, "using")
            where = self.sql(expression, "where")
            returning = self.sql(expression, "returning")
            limit = self.sql(expression, "limit")
            tables = self.expressions(expression, key="tables")
            tables = f" {tables}" if tables else ""

            if using:
                using = f" USING {using}" if using else ""
                where = where.replace("WHERE", "ON")
            else:
                this = f"FROM {this}" if this else ""

            if self.RETURNING_END:
                expression_sql = f" {this}{using}{where}{returning}{limit}"
            else:
                expression_sql = f"{returning}{this}{where}{limit}"

            if using:
                return self.prepend_ctes(expression, f"MERGE INTO {tables}{expression_sql} WHEN MATCHED THEN DELETE;")

            return self.prepend_ctes(expression, f"DELETE{tables}{expression_sql};")

        def converttimezone_sql(self, expression: exp.ConvertTimezone):
            func = "CONVERT_TIMEZONE"
            expr = expression.args["tgtTZ"]
            if len(expression.args) == 3 and expression.args.get("this"):
                expr = expression.args["this"]

            result = self.func(func, expression.args["srcTZ"], expr)
            if len(expression.args) == 3:
                result = self.func(func, expression.args["srcTZ"], expression.args["tgtTZ"], expr)

            return result

        def strtok_sql(self, expression: local_expression.StrTok) -> str:
            """
            :param expression: local_expression.StrTok expression to be parsed
            :return: Converted expression (SPLIT_PART) compatible with Databricks
            """
            # To handle default delimiter
            if expression.expression:
                delimiter = expression.expression.name
            else:
                delimiter = " "

            # Handle String and Table columns
            if expression.name and isinstance(expression.name, str):
                expr_name = f"'{expression.name}'"
            else:
                expr_name = expression.args["this"]

            # Handle Partition Number
            if len(expression.args) == 3 and expression.args.get("partNum"):
                part_num = expression.args["partNum"]
            else:
                part_num = 1

            return f"SPLIT_PART({expr_name}, '{delimiter}', {part_num})"

        def splitpart_sql(self, expression: local_expression.SplitPart) -> str:
            """
            :param expression: local_expression.SplitPart expression to be parsed
            :return: Converted expression (SPLIT_PART) compatible with Databricks
            """
            expr_name = self.sql(expression.this)
            delimiter = self.sql(expression.expression)
            part_num = self.sql(expression.args["partNum"])
            return f"SPLIT_PART({expr_name}, {delimiter}, {part_num})"

        def transaction_sql(self, expression: exp.Transaction) -> str:
            """
            Skip begin command
            :param expression:
            :return: Empty string for unsupported operation
            """
            return ""

        def rollback_sql(self, expression: exp.Rollback) -> str:
            """
            Skip rollback command
            :param expression:
            :return: Empty string for unsupported operation
            """
            return ""

        def commit_sql(self, expression: exp.Commit) -> str:
            """
            Skip commit command
            :param expression:
            :return: Empty string for unsupported operation
            """
            return ""

        def command_sql(self, expression: exp.Command) -> str:
            """
            Skip any session, stream, task related commands
            :param expression:
            :return: Empty string for unsupported operations or objects
            """
            filtered_commands = [
                "CREATE",
                "ALTER",
                "DESCRIBE",
                "DROP",
                "SHOW",
                "EXECUTE",
            ]
            ignored_objects = [
                "STREAM",
                "TASK",
                "STREAMS",
                "TASKS",
                "SESSION",
            ]

            command = self.sql(expression, "this").upper()
            expr = expression.text("expression").strip()
            obj = re.split(r"\s+", expr, maxsplit=2)[0].upper() if expr else ""
            if command in filtered_commands and obj in ignored_objects:
                return ""
            return f"{command} {expr}"

        def currenttimestamp_sql(self, _: exp.CurrentTimestamp) -> str:
            return self.func("CURRENT_TIMESTAMP")

        def update_sql(self, expression: exp.Update) -> str:
            this = self.sql(expression, "this")
            set_sql = self.expressions(expression, flat=True)
            from_sql = self.sql(expression, "from")
            where_sql = self.sql(expression, "where")
            returning = self.sql(expression, "returning")
            order = self.sql(expression, "order")
            limit = self.sql(expression, "limit")

            if from_sql:
                from_sql = from_sql.replace("FROM", "USING", 1)
                where_sql = where_sql.replace("WHERE", "ON")

            if self.RETURNING_END:
                expression_sql = f"{from_sql}{where_sql}{returning}"
            else:
                expression_sql = f"{returning}{from_sql}{where_sql}"

            if from_sql:
                sql = f"MERGE INTO {this}{expression_sql} WHEN MATCHED THEN UPDATE SET {set_sql}{order}{limit}"
            else:
                sql = f"UPDATE {this} SET {set_sql}{expression_sql}{order}{limit}"

            return self.prepend_ctes(expression, sql)

        def struct_sql(self, expression: exp.Struct) -> str:
            expression.set(
                "expressions",
                [
                    (
                        exp.alias_(
                            e.expression, e.name if hasattr(e.this, "is_string") and e.this.is_string else e.this
                        )
                        if isinstance(e, exp.PropertyEQ)
                        else e
                    )
                    for e in expression.expressions
                ],
            )

            return self.function_fallback_sql(expression)

        def anonymous_sql(self: SqlglotDatabricks.Generator, expression: exp.Anonymous) -> str:
            if expression.this == "EDITDISTANCE":
                return self.func("LEVENSHTEIN", *expression.expressions)
            if expression.this == "TO_TIMESTAMP":
                return self.sql(
                    exp.Cast(this=expression.expressions[0], to=exp.DataType(this=exp.DataType.Type.TIMESTAMP))
                )

            return self.func(self.sql(expression, "this"), *expression.expressions)

        def order_sql(self, expression: exp.Order, flat: bool = False) -> str:
            if isinstance(expression.parent, exp.Window):
                for ordered_expression in expression.expressions:
                    if isinstance(ordered_expression, exp.Ordered) and ordered_expression.args.get('desc') is None:
                        ordered_expression.args['desc'] = False
            return super().order_sql(expression, flat)

        def add_column_sql(self, expression: exp.Alter) -> str:
            # Final output contains ADD COLUMN before each column
            # This function will handle this issue and return the final output
            columns = self.expressions(expression, key="actions", flat=True)
            return f"ADD COLUMN {columns}"
