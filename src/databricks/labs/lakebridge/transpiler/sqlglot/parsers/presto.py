import logging
from sqlglot.dialects.presto import Presto as presto
from sqlglot import exp
from sqlglot.helper import seq_get
from sqlglot.errors import ParseError
from sqlglot.tokens import TokenType

from databricks.labs.lakebridge.transpiler.sqlglot import local_expression


logger = logging.getLogger(__name__)


def _build_approx_percentile(args: list) -> exp.Expression:
    if len(args) == 4:
        arg3 = seq_get(args, 3)
        try:
            number = float(arg3.this) if arg3 is not None else 0
            return exp.ApproxQuantile(
                this=seq_get(args, 0),
                weight=seq_get(args, 1),
                quantile=seq_get(args, 2),
                accuracy=exp.Literal(this=f'{int((1/number) * 100)} ', is_string=False),
            )
        except ValueError as exc:
            raise ParseError(f"Expected a string representation of a number for argument 2, but got {arg3}") from exc
    if len(args) == 3:
        arg2 = seq_get(args, 2)
        try:
            number = float(arg2.this) if arg2 is not None else 0
            return exp.ApproxQuantile(
                this=seq_get(args, 0),
                quantile=seq_get(args, 1),
                accuracy=exp.Literal(this=f'{int((1/number) * 100)}', is_string=False),
            )
        except ValueError as exc:
            raise ParseError(f"Expected a string representation of a number for argument 2, but got {arg2}") from exc
    return exp.ApproxQuantile.from_arg_list(args)


def _build_any_keys_match(args: list) -> local_expression.ArrayExists:
    return local_expression.ArrayExists(
        this=local_expression.MapKeys(this=seq_get(args, 0)), expression=seq_get(args, 1)
    )


def _build_str_position(args: list) -> local_expression.Locate:
    # TODO the 3rd param in presto strpos and databricks locate has different implementation.
    # For now we haven't implemented the logic same as presto for 3rd param.
    # Users should be vigilant when using 3 param function in presto strpos.
    if len(args) == 3:
        msg = (
            "*Warning:: The third parameter in Presto's `strpos` function and Databricks' `locate` function "
            "have different implementations. Please exercise caution when using the three-parameter version "
            "of the `strpos` function in Presto."
        )
        logger.warning(msg)
        return local_expression.Locate(substring=seq_get(args, 1), this=seq_get(args, 0), position=seq_get(args, 2))
    return local_expression.Locate(substring=seq_get(args, 1), this=seq_get(args, 0))


def _build_array_average(args: list) -> exp.Reduce:
    return exp.Reduce(
        this=exp.ArrayFilter(
            this=seq_get(args, 0),
            expression=exp.Lambda(
                this=exp.Not(this=exp.Is(this=exp.Identifier(this="x", quoted=False), expression=exp.Null())),
                expressions=[exp.Identifier(this="x", quoted=False)],
            ),
        ),
        initial=local_expression.NamedStruct(
            expressions=[
                exp.Literal(this="sum", is_string=True),
                exp.Cast(this=exp.Literal(this="0", is_string=False), to=exp.DataType(this="DOUBLE")),
                exp.Literal(this="cnt", is_string=True),
                exp.Literal(this="0", is_string=False),
            ],
        ),
        merge=exp.Lambda(
            this=local_expression.NamedStruct(
                expressions=[
                    exp.Literal(this="sum", is_string=True),
                    exp.Add(
                        this=exp.Dot(
                            this=exp.Identifier(this="acc", quoted=False),
                            expression=exp.Identifier(this="sum", quoted=False),
                        ),
                        expression=exp.Identifier(this="x", quoted=False),
                    ),
                    exp.Literal(this="cnt", is_string=True),
                    exp.Add(
                        this=exp.Dot(
                            this=exp.Identifier(this="acc", quoted=False),
                            expression=exp.Identifier(this="cnt", quoted=False),
                        ),
                        expression=exp.Literal(this="1", is_string=False),
                    ),
                ],
            ),
            expressions=[exp.Identifier(this="acc", quoted=False), exp.Identifier(this="x", quoted=False)],
        ),
        finish=exp.Lambda(
            this=exp.Anonymous(
                this="try_divide",
                expressions=[
                    exp.Dot(
                        this=exp.Identifier(this="acc", quoted=False),
                        expression=exp.Identifier(this="sum", quoted=False),
                    ),
                    exp.Dot(
                        this=exp.Identifier(this="acc", quoted=False),
                        expression=exp.Identifier(this="cnt", quoted=False),
                    ),
                ],
            ),
            expressions=[exp.Identifier(this="acc", quoted=False)],
        ),
    )


def _build_json_size(args: list):
    return exp.Case(
        ifs=[
            exp.If(
                this=exp.Like(
                    this=local_expression.GetJsonObject(
                        this=exp.Column(this=seq_get(args, 0)),
                        path=exp.Column(this=seq_get(args, 1)),
                    ),
                    expression=exp.Literal(this="{%", is_string=True),
                ),
                true=exp.ArraySize(
                    this=exp.Anonymous(
                        this="from_json",
                        expressions=[
                            local_expression.GetJsonObject(
                                this=exp.Column(this=seq_get(args, 0)),
                                path=exp.Column(this=seq_get(args, 1)),
                            ),
                            exp.Literal(this="map<string,string>", is_string=True),
                        ],
                    )
                ),
            ),
            exp.If(
                this=exp.Like(
                    this=local_expression.GetJsonObject(
                        this=exp.Column(this=seq_get(args, 0)),
                        path=exp.Column(this=seq_get(args, 1)),
                    ),
                    expression=exp.Literal(this="[%", is_string=True),
                ),
                true=exp.ArraySize(
                    this=exp.Anonymous(
                        this="from_json",
                        expressions=[
                            local_expression.GetJsonObject(
                                this=exp.Column(this=seq_get(args, 0)),
                                path=exp.Column(this=seq_get(args, 1)),
                            ),
                            exp.Literal(this="array<string>", is_string=True),
                        ],
                    )
                ),
            ),
            exp.If(
                this=exp.Not(
                    this=exp.Is(
                        this=local_expression.GetJsonObject(
                            this=exp.Column(this=seq_get(args, 0)),
                            path=exp.Column(this=seq_get(args, 1)),
                        ),
                        expression=exp.Null(),
                    )
                ),
                true=exp.Literal(this="0", is_string=False),
            ),
        ],
        default=exp.Null(),
    )


class Presto(presto):

    class Parser(presto.Parser):
        VALUES_FOLLOWED_BY_PAREN = False

        FUNCTIONS = {
            **presto.Parser.FUNCTIONS,
            "APPROX_PERCENTILE": _build_approx_percentile,
            "STRPOS": _build_str_position,
            "ANY_KEYS_MATCH": _build_any_keys_match,
            "ARRAY_AVERAGE": _build_array_average,
            "JSON_SIZE": _build_json_size,
            "FORMAT_DATETIME": local_expression.DateFormat.from_arg_list,
        }

    class Tokenizer(presto.Tokenizer):
        KEYWORDS = {
            **presto.Tokenizer.KEYWORDS,
            "JSON": TokenType.TEXT,
        }
