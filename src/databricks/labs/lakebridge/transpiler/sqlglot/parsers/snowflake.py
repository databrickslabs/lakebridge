import logging
import re

from sqlglot import expressions as exp
from sqlglot.dialects.dialect import build_date_delta as parse_date_delta, build_formatted_time
from sqlglot.dialects.snowflake import Snowflake as SqlglotSnowflake
from sqlglot.errors import TokenError, ParseError
from sqlglot.helper import is_int, seq_get
from sqlglot.optimizer.simplify import simplify_literals
from sqlglot.parser import build_var_map as parse_var_map
from sqlglot.tokens import Token, TokenType
from sqlglot.trie import new_trie

from databricks.labs.lakebridge.transpiler.sqlglot import local_expression

logger = logging.getLogger(__name__)
# pylint: disable=protected-access
""" SF Supported Date and Time Parts:
    https://docs.snowflake.com/en/sql-reference/functions-date-time#label-supported-date-time-parts
    Covers DATEADD, DATEDIFF, DATE_TRUNC, LAST_DAY
"""
DATE_DELTA_INTERVAL = {
    "years": "year",
    "year": "year",
    "yrs": "year",
    "yr": "year",
    "yyyy": "year",
    "yyy": "year",
    "yy": "year",
    "y": "year",
    "quarters": "quarter",
    "quarter": "quarter",
    "qtrs": "quarter",
    "qtr": "quarter",
    "q": "quarter",
    "months": "month",
    "month": "month",
    "mons": "month",
    "mon": "month",
    "mm": "month",
    "weekofyear": "week",
    "week": "week",
    "woy": "week",
    "wy": "week",
    "wk": "week",
    "w": "week",
    "dayofmonth": "day",
    "days": "day",
    "day": "day",
    "dd": "day",
    "d": "day",
}

rank_functions = (
    local_expression.CumeDist,
    exp.FirstValue,
    exp.LastValue,
    local_expression.NthValue,
    local_expression.Ntile,
)


def _parse_to_timestamp(args: list) -> exp.StrToTime | exp.UnixToTime | exp.TimeStrToTime:
    if len(args) == 2:
        first_arg, second_arg = args
        if second_arg.is_string:
            # case: <string_expr> [ , <format> ]
            return build_formatted_time(exp.StrToTime, "snowflake", default=True)(args)
        return exp.UnixToTime(this=first_arg, scale=second_arg)

    # The first argument might be an expression like 40 * 365 * 86400, so we try to
    # reduce it using `simplify_literals` first and then check if it's a Literal.
    first_arg = seq_get(args, 0)
    if not isinstance(simplify_literals(first_arg, root=True), exp.Literal):
        # case: <variant_expr> or other expressions such as columns
        return exp.TimeStrToTime.from_arg_list(args)

    if first_arg.is_string:
        if is_int(first_arg.this):
            # case: <integer>
            return exp.UnixToTime.from_arg_list(args)

        # case: <date_expr>
        return build_formatted_time(exp.StrToTime, "snowflake", default=True)(args)

    # case: <numeric_expr>
    return exp.UnixToTime.from_arg_list(args)


def _parse_date_add(args: list) -> exp.DateAdd:
    return exp.DateAdd(this=seq_get(args, 2), expression=seq_get(args, 1), unit=seq_get(args, 0))


def _parse_split_part(args: list) -> local_expression.SplitPart:
    if len(args) != 3:
        err_msg = f"Error Parsing args `{args}`. Number of args must be 3, given {len(args)}"
        raise ParseError(err_msg)
    part_num_literal = seq_get(args, 2)
    part_num_if = None
    if isinstance(part_num_literal, exp.Literal):
        # In Snowflake if the partNumber is 0, it is treated as 1.
        # Please refer to https://docs.snowflake.com/en/sql-reference/functions/split_part
        if part_num_literal.is_int and int(part_num_literal.name) == 0:
            part_num_literal = exp.Literal.number(1)
    else:
        cond = exp.EQ(this=part_num_literal, expression=exp.Literal.number(0))
        part_num_if = exp.If(this=cond, true=exp.Literal.number(1), false=part_num_literal)

    part_num = part_num_if if part_num_if is not None else part_num_literal
    return local_expression.SplitPart(this=seq_get(args, 0), expression=seq_get(args, 1), partNum=part_num)


def _div0_to_if(args: list) -> exp.If:
    cond = exp.EQ(this=seq_get(args, 1), expression=exp.Literal.number(0))
    true = exp.Literal.number(0)
    false = exp.Div(this=seq_get(args, 0), expression=seq_get(args, 1))
    return exp.If(this=cond, true=true, false=false)


def _div0null_to_if(args: list) -> exp.If:
    cond = exp.Or(
        this=exp.EQ(this=seq_get(args, 1), expression=exp.Literal.number(0)),
        expression=exp.Is(this=seq_get(args, 1), expression=exp.Null()),
    )
    true = exp.Literal.number(0)
    false = exp.Div(this=seq_get(args, 0), expression=seq_get(args, 1))
    return exp.If(this=cond, true=true, false=false)


def _parse_json_extract_path_text(args: list) -> local_expression.JsonExtractPathText:
    if len(args) != 2:
        err_message = f"Error Parsing args `{args}`. Number of args must be 2, given {len(args)}"
        raise ParseError(err_message)
    return local_expression.JsonExtractPathText(this=seq_get(args, 0), path_name=seq_get(args, 1))


def _parse_array_contains(args: list) -> exp.ArrayContains:
    if len(args) != 2:
        err_message = f"Error Parsing args `{args}`. Number of args must be 2, given {len(args)}"
        raise ParseError(err_message)
    return exp.ArrayContains(this=seq_get(args, 1), expression=seq_get(args, 0))


def _parse_dayname(args: list) -> local_expression.DateFormat:
    """
        * E, EE, EEE, returns short day name (Mon)
        * EEEE, returns full day name (Monday)
    :param args: node expression
    :return: DateFormat with `E` format
    """
    if len(args) != 1:
        err_message = f"Error Parsing args `{args}`. Number of args must be 1, given {len(args)}"
        raise ParseError(err_message)
    return local_expression.DateFormat(this=seq_get(args, 0), expression=exp.Literal.string("E"))


def _parse_trytonumber(args: list) -> local_expression.TryToNumber:
    if len(args) == 1:
        msg = f"""*Warning:: Parsing args `{args}`:
                             * `format` is missing
                             * assuming defaults `precision`[38] and `scale`[0]
                          """
        logger.warning(msg)
    elif len(args) == 3:
        msg = f"""Error Parsing args `{args}`:
                             * `format` is required
                             * `precision` and `scale` both are required [if specified]
                          """
        raise ParseError(msg)

    if len(args) == 4:
        return local_expression.TryToNumber(
            this=seq_get(args, 0), expression=seq_get(args, 1), precision=seq_get(args, 2), scale=seq_get(args, 3)
        )

    return local_expression.TryToNumber(this=seq_get(args, 0), expression=seq_get(args, 1))


def _parse_monthname(args: list) -> local_expression.DateFormat:
    if len(args) != 1:
        err_message = f"Error Parsing args `{args}`. Number of args must be 1, given {len(args)}"
        raise ParseError(err_message)
    return local_expression.DateFormat(this=seq_get(args, 0), expression=exp.Literal.string("MMM"))


def _parse_object_construct(args: list) -> exp.StarMap | exp.Struct:
    expression = parse_var_map(args)

    if isinstance(expression, exp.StarMap):
        return exp.Struct(expressions=[expression.this])

    return exp.Struct(
        expressions=[
            exp.PropertyEQ(this=k.this, expression=v) for k, v in zip(expression.keys, expression.values, strict=False)
        ]
    )


def _parse_to_boolean(args: list, *, error=False) -> local_expression.ToBoolean:
    this_arg = seq_get(args, 0)
    return local_expression.ToBoolean(this=this_arg, raise_error=exp.Literal.number(1 if error else 0))


def _parse_tonumber(args: list) -> local_expression.ToNumber:
    if len(args) > 4:
        error_msg = f"""Error Parsing args args:
                          * Number of args cannot be more than `4`, given `{len(args)}`
                          """
        raise ParseError(error_msg)

    match len(args):
        case 1:
            msg = (
                "Precision and Scale are not specified, assuming defaults `precision`[38] and `scale`[0]. "
                "If Format is not specified, it will be inferred as simple cast as decimal"
            )
            logger.warning(msg)
            return local_expression.ToNumber(this=seq_get(args, 0))
        case 3:
            msg = "If Format is not specified, it will be inferred as simple cast as decimal"
            logger.warning(msg)
            return local_expression.ToNumber(this=seq_get(args, 0), precision=seq_get(args, 1), scale=seq_get(args, 2))
        case 4:
            return local_expression.ToNumber(
                this=seq_get(args, 0), expression=seq_get(args, 1), precision=seq_get(args, 2), scale=seq_get(args, 3)
            )

    return local_expression.ToNumber(this=seq_get(args, 0), expression=seq_get(args, 1))


def contains_expression(expr, target_type):
    if isinstance(expr, target_type):
        return True
    if hasattr(expr, 'this') and contains_expression(expr.this, target_type):
        return True
    if hasattr(expr, 'expressions'):
        for sub_expr in expr.expressions:
            if contains_expression(sub_expr, target_type):
                return True
    return False


def _parse_sha2(args: list) -> exp.SHA2:
    if len(args) == 1:
        return exp.SHA2(this=seq_get(args, 0), length=exp.Literal.number(256))
    return exp.SHA2(this=seq_get(args, 0), length=seq_get(args, 1))


def _parse_last_day(args: list) -> exp.LastDay | exp.DateSub:
    if len(args) == 1:
        return exp.LastDay.from_arg_list(args)

    date_part = seq_get(args, 1)
    if isinstance(date_part, exp.Literal):
        date_part = DATE_DELTA_INTERVAL.get(date_part.this.lower(), None)
    elif isinstance(date_part, exp.Column):
        date_part = DATE_DELTA_INTERVAL.get(date_part.name.lower(), None)

    if date_part is None or date_part.lower() not in ('year', 'quarter', 'month', 'week'):
        raise ParseError(f'Invalid date part {date_part} for last_day')

    date_trunc_expr = local_expression.DateTrunc(this=seq_get(args, 0), unit=exp.Literal.string(date_part))
    # Add one date part
    date_add_expr = parse_date_delta(exp.DateAdd, unit_mapping=DATE_DELTA_INTERVAL)(
        [exp.Literal.string(date_part), exp.Literal.number(1), date_trunc_expr]
    )

    # Subtract one day
    date_sub_expr = parse_date_delta(exp.DateSub, unit_mapping=DATE_DELTA_INTERVAL)(
        [exp.Literal.string('DAY'), exp.Literal.number(1), date_add_expr]
    )

    return date_sub_expr


class Snowflake(SqlglotSnowflake):
    # Instantiate Snowflake Dialect
    snowflake = SqlglotSnowflake()

    class Tokenizer(SqlglotSnowflake.Tokenizer):

        COMMENTS = ["--", "//", ("/*", "*/")]
        STRING_ESCAPES = ["\\", "'"]

        CUSTOM_TOKEN_MAP = {
            r"(?i)CREATE\s+OR\s+REPLACE\s+PROCEDURE": TokenType.PROCEDURE,
            r"(?i)var\s+\w+\s+=\s+\w+?": TokenType.VAR,
        }

        SINGLE_TOKENS = {
            **SqlglotSnowflake.Tokenizer.SINGLE_TOKENS,
            "&": TokenType.PARAMETER,  # https://docs.snowflake.com/en/user-guide/snowsql-use#substituting-variables-in-a-session
            "!": TokenType.COMMAND,
        }

        KEYWORDS = {**SqlglotSnowflake.Tokenizer.KEYWORDS}
        # DEC is not a reserved keyword in Snowflake it can be used as table alias
        KEYWORDS.pop("DEC")

        @classmethod
        def update_keywords(cls, new_key_word_dict):
            cls.KEYWORDS = new_key_word_dict | cls.KEYWORDS

        @classmethod
        def merge_trie(cls, parent_trie, curr_trie):
            merged_trie = {}
            logger.debug(f"The Parent Trie is {parent_trie}")
            logger.debug(f"The Input Trie is {curr_trie}")
            for key in set(parent_trie.keys()) | set(curr_trie.keys()):  # Get all unique keys from both tries
                if key in parent_trie and key in curr_trie:  # If the key is in both tries, merge the subtries
                    if isinstance(parent_trie[key], dict) and isinstance(curr_trie[key], dict):
                        logger.debug(f"New trie inside the key is {curr_trie}")
                        logger.debug(f"Parent trie inside the key is {parent_trie}")
                        merged_trie[key] = cls.merge_trie(parent_trie[key], curr_trie[key])
                        logger.debug(f"Merged Trie is {merged_trie}")
                    elif isinstance(parent_trie[key], dict):
                        merged_trie[key] = parent_trie[key]
                    else:
                        merged_trie[key] = curr_trie[key]
                elif key in parent_trie:  # If the key is only in trie1, add it to the merged trie
                    merged_trie[key] = parent_trie[key]
                else:  # If the key is only in trie2, add it to the merged trie
                    merged_trie[key] = curr_trie[key]
            return merged_trie

        @classmethod
        def update_keyword_trie(
            cls,
            curr_trie,
            parent_trie=None,
        ):
            if parent_trie is None:
                parent_trie = cls._KEYWORD_TRIE
            cls.KEYWORD_TRIE = cls.merge_trie(parent_trie, curr_trie)

        def match_strings_token_dict(self, string, pattern_dict):
            result_dict = {}
            for pattern in pattern_dict:
                matches = re.finditer(pattern, string, re.MULTILINE | re.IGNORECASE | re.DOTALL)
                for _, match in enumerate(matches, start=1):
                    result_dict[match.group().upper()] = pattern_dict[pattern]
            return result_dict

        def match_strings_list(self, string, pattern_dict):
            result = []
            for pattern in pattern_dict:
                matches = re.finditer(pattern, string, re.MULTILINE | re.IGNORECASE | re.DOTALL)
                for _, match in enumerate(matches, start=1):
                    result.append(match.group().upper())
            return result

        def tokenize(self, sql: str) -> list[Token]:
            """Returns a list of tokens corresponding to the SQL string `sql`."""
            self.reset()
            self.sql = sql
            # Update Keywords
            ref_dict = self.match_strings_token_dict(sql, self.CUSTOM_TOKEN_MAP)
            self.update_keywords(ref_dict)
            # Update Keyword Trie
            custom_trie = new_trie(self.match_strings_list(sql, self.CUSTOM_TOKEN_MAP))
            logger.debug(
                f"The New Trie after adding the REF, VAR and IF ELSE blocks "
                f"based on {self.CUSTOM_TOKEN_MAP}, is \n\n {custom_trie}"
            )
            self.update_keyword_trie(custom_trie)
            logger.debug(f"Updated New Trie is {custom_trie}")
            # Parent Code
            self.size = len(sql)
            try:
                self._scan()
            except Exception as e:
                start = self._current - 50
                end = self._current + 50
                start = start if start > 0 else 0
                end = end if end < self.size else self.size - 1
                context = self.sql[start:end]
                msg = f"Error tokenizing '{context}'"
                raise TokenError(msg) from e
            return self.tokens

    class Parser(SqlglotSnowflake.Parser):
        FUNCTIONS = {
            **SqlglotSnowflake.Parser.FUNCTIONS,
            "ARRAY_AGG": exp.ArrayAgg.from_arg_list,
            "STRTOK_TO_ARRAY": local_expression.Split.from_arg_list,
            "DATE_FROM_PARTS": local_expression.MakeDate.from_arg_list,
            "CONVERT_TIMEZONE": local_expression.ConvertTimeZone.from_arg_list,
            "TRY_TO_DATE": local_expression.TryToDate.from_arg_list,
            "TRY_TO_TIMESTAMP": local_expression.TryToTimestamp.from_arg_list,
            "STRTOK": local_expression.StrTok.from_arg_list,
            "SPLIT_PART": _parse_split_part,
            "TIMESTAMPADD": _parse_date_add,
            "TRY_TO_DECIMAL": _parse_trytonumber,
            "TRY_TO_NUMBER": _parse_trytonumber,
            "TRY_TO_NUMERIC": _parse_trytonumber,
            "DATEADD": parse_date_delta(exp.DateAdd, unit_mapping=DATE_DELTA_INTERVAL),
            "DATEDIFF": parse_date_delta(exp.DateDiff, unit_mapping=DATE_DELTA_INTERVAL),
            "IS_INTEGER": local_expression.IsInteger.from_arg_list,
            "DIV0": _div0_to_if,
            "DIV0NULL": _div0null_to_if,
            "JSON_EXTRACT_PATH_TEXT": _parse_json_extract_path_text,
            "BITOR_AGG": local_expression.BitOr.from_arg_list,
            "ARRAY_CONTAINS": _parse_array_contains,
            "DAYNAME": _parse_dayname,
            "BASE64_ENCODE": exp.ToBase64.from_arg_list,
            "BASE64_DECODE_STRING": exp.FromBase64.from_arg_list,
            "TRY_BASE64_DECODE_STRING": exp.FromBase64.from_arg_list,
            "ARRAY_CONSTRUCT_COMPACT": local_expression.ArrayConstructCompact.from_arg_list,
            "ARRAY_INTERSECTION": local_expression.ArrayIntersection.from_arg_list,
            "ARRAY_SLICE": local_expression.ArraySlice.from_arg_list,
            "MONTHNAME": _parse_monthname,
            "MONTH_NAME": _parse_monthname,
            "OBJECT_CONSTRUCT": _parse_object_construct,
            "OBJECT_KEYS": local_expression.ObjectKeys.from_arg_list,
            "TRY_PARSE_JSON": exp.ParseJSON.from_arg_list,
            "TIMEDIFF": parse_date_delta(exp.DateDiff, unit_mapping=DATE_DELTA_INTERVAL),
            "TIMESTAMPDIFF": parse_date_delta(exp.DateDiff, unit_mapping=DATE_DELTA_INTERVAL),
            "TIMEADD": _parse_date_add,
            "TO_BOOLEAN": lambda args: _parse_to_boolean(args, error=True),
            "TO_DECIMAL": _parse_tonumber,
            "TO_DOUBLE": local_expression.ToDouble.from_arg_list,
            "TO_NUMBER": _parse_tonumber,
            "TO_NUMERIC": _parse_tonumber,
            "TO_OBJECT": local_expression.ToObject.from_arg_list,
            "TO_TIME": _parse_to_timestamp,
            "TIMESTAMP_FROM_PARTS": local_expression.TimestampFromParts.from_arg_list,
            "TO_VARIANT": local_expression.ToVariant.from_arg_list,
            "TRY_TO_BOOLEAN": lambda args: _parse_to_boolean(args, error=False),
            "UUID_STRING": local_expression.UUID.from_arg_list,
            "SYSDATE": exp.CurrentTimestamp.from_arg_list,
            "TRUNC": lambda args: local_expression.DateTrunc(unit=seq_get(args, 1), this=seq_get(args, 0)),
            "APPROX_PERCENTILE": exp.ApproxQuantile.from_arg_list,
            "NTH_VALUE": local_expression.NthValue.from_arg_list,
            "MEDIAN": local_expression.Median.from_arg_list,
            "CUME_DIST": local_expression.CumeDist.from_arg_list,
            "DENSE_RANK": local_expression.DenseRank.from_arg_list,
            "RANK": local_expression.Rank.from_arg_list,
            "PERCENT_RANK": local_expression.PercentRank.from_arg_list,
            "NTILE": local_expression.Ntile.from_arg_list,
            "TO_ARRAY": local_expression.ToArray.from_arg_list,
            "SHA2": _parse_sha2,
            "LAST_DAY": _parse_last_day,
            "ARRAY_FLATTEN": exp.Flatten.from_arg_list,
        }

        FUNCTION_PARSERS = {
            **SqlglotSnowflake.Parser.FUNCTION_PARSERS,
            "LISTAGG": lambda self: self._parse_list_agg(),
        }

        PLACEHOLDER_PARSERS = {
            **SqlglotSnowflake.Parser.PLACEHOLDER_PARSERS,
            TokenType.PARAMETER: lambda self: self._parse_parameter(),
        }

        FUNC_TOKENS = {*SqlglotSnowflake.Parser.FUNC_TOKENS, TokenType.COLLATE}

        COLUMN_OPERATORS = {
            **SqlglotSnowflake.Parser.COLUMN_OPERATORS,
        }

        TIMESTAMPS: set[TokenType] = SqlglotSnowflake.Parser.TIMESTAMPS.copy() - {TokenType.TIME}

        RANGE_PARSERS = {
            **SqlglotSnowflake.Parser.RANGE_PARSERS,
        }

        ALTER_PARSERS = {**SqlglotSnowflake.Parser.ALTER_PARSERS}

        def _parse_list_agg(self) -> exp.GroupConcat:
            if self._match(TokenType.DISTINCT):
                args: list[exp.Expression] = [self.expression(exp.Distinct, expressions=[self._parse_conjunction()])]
                if self._match(TokenType.COMMA):
                    args.extend(self._parse_csv(self._parse_conjunction))
            else:
                args = self._parse_csv(self._parse_conjunction)

            return self.expression(exp.GroupConcat, this=args[0], separator=seq_get(args, 1))

        def _parse_types(
            self, check_func: bool = False, schema: bool = False, allow_identifiers: bool = True
        ) -> exp.Expression | None:
            this = super()._parse_types(check_func=check_func, schema=schema, allow_identifiers=allow_identifiers)
            # https://docs.snowflake.com/en/sql-reference/data-types-numeric Numeric datatype alias
            if (
                isinstance(this, exp.DataType)
                and this.is_type("numeric", "decimal", "number", "integer", "int", "smallint", "bigint")
                and not this.expressions
            ):
                return exp.DataType.build("DECIMAL(38,0)")
            return this

        def _parse_parameter(self):
            wrapped = self._match(TokenType.L_BRACE)
            this = self._parse_var() or self._parse_identifier() or self._parse_primary()
            self._match(TokenType.R_BRACE)
            suffix: exp.Expression | None = None
            if not self._match(TokenType.SPACE) or self._match(TokenType.DOT):
                suffix = self._parse_var() or self._parse_identifier() or self._parse_primary()

            return self.expression(local_expression.Parameter, this=this, wrapped=wrapped, suffix=suffix)

        def _parse_window(self, this: exp.Expression | None, alias: bool = False) -> exp.Expression | None:
            window = super()._parse_window(this=this, alias=alias)
            # Adding default window frame for the rank-related functions in snowflake
            if window and contains_expression(window.this, rank_functions) and window.args.get('spec') is None:
                window.args['spec'] = self.expression(
                    exp.WindowSpec,
                    kind="ROWS",
                    start="UNBOUNDED",
                    start_side="PRECEDING",
                    end="UNBOUNDED",
                    end_side="FOLLOWING",
                )
            return window

        def _parse_alter_table_add(self) -> list[exp.Expression]:
            index = self._index - 1
            if self._match_set(self.ADD_CONSTRAINT_TOKENS, advance=False):
                return self._parse_csv(
                    lambda: self.expression(exp.AddConstraint, expressions=self._parse_csv(self._parse_constraint))
                )

            self._retreat(index)
            if not self.ALTER_TABLE_ADD_REQUIRED_FOR_EACH_COLUMN and self._match_text_seq("ADD"):
                return self._parse_wrapped_csv(self._parse_field_def, optional=True)

            if self._match_text_seq("ADD", "COLUMN"):
                schema = self._parse_schema()
                if schema:
                    return [schema]
                # return self._parse_csv in case of COLUMNS are not enclosed in brackets ()
                return self._parse_csv(self._parse_field_def)

            return self._parse_wrapped_csv(self._parse_add_column, optional=True)
