import pytest
from sqlglot import expressions as exp
from sqlglot import parse_one
from sqlglot.expressions import Column

from databricks.labs.lakebridge.reconcile.query_builder.expression_generator import (
    array_sort,
    array_to_string,
    build_between,
    build_column,
    build_from_clause,
    build_if,
    build_join_clause,
    build_literal,
    build_sub,
    build_where_clause,
    coalesce,
    concat,
    get_hash_transform,
    json_format,
    lower,
    md5,
    sha2,
    sort_array,
    to_char,
    trim,
)
from databricks.labs.lakebridge.transpiler.sqlglot.dialect_utils import get_dialect


def test_coalesce(expr):
    assert coalesce(expr, "NA", True).sql() == "SELECT COALESCE(col1, 'NA') FROM DUAL"
    assert coalesce(expr, "0", False).sql() == "SELECT COALESCE(col1, 0) FROM DUAL"
    assert coalesce(expr).sql() == "SELECT COALESCE(col1, 0) FROM DUAL"


def test_trim(expr):
    assert trim(expr).sql() == "SELECT TRIM(col1) FROM DUAL"

    nested_expr = parse_one("select coalesce(col1,' ') FROM DUAL")
    assert trim(nested_expr).sql() == "SELECT COALESCE(TRIM(col1), ' ') FROM DUAL"


def test_json_format():
    expr = parse_one("SELECT col1 FROM DUAL")

    assert json_format(expr).sql() == "SELECT JSON_FORMAT(col1) FROM DUAL"
    assert json_format(expr).sql(dialect="databricks") == "SELECT TO_JSON(col1) FROM DUAL"
    assert json_format(expr).sql(dialect="snowflake") == "SELECT TO_JSON(col1) FROM DUAL"


def test_sort_array(expr):
    assert sort_array(expr).sql() == "SELECT SORT_ARRAY(col1, TRUE) FROM DUAL"
    assert sort_array(expr, asc=False).sql() == "SELECT SORT_ARRAY(col1, FALSE) FROM DUAL"


def test_to_char(expr):
    assert to_char(expr).sql(dialect="oracle") == "SELECT TO_CHAR(col1) FROM DUAL"
    assert to_char(expr, to_format='YYYY-MM-DD').sql(dialect="oracle") == "SELECT TO_CHAR(col1, 'YYYY-MM-DD') FROM DUAL"


def test_array_to_string(expr):
    assert array_to_string(expr).sql() == "SELECT ARRAY_TO_STRING(col1, ',') FROM DUAL"
    assert array_to_string(expr, null_replacement='NA').sql() == "SELECT ARRAY_TO_STRING(col1, ',', 'NA') FROM DUAL"


def test_array_sort(expr):
    assert array_sort(expr).sql() == "SELECT ARRAY_SORT(col1, TRUE) FROM DUAL"
    assert array_sort(expr, asc=False).sql() == "SELECT ARRAY_SORT(col1, FALSE) FROM DUAL"


def test_build_column():
    # test build_column without alias and column as str expr
    assert build_column(this="col1") == exp.Column(this=exp.Identifier(this="col1", quoted=False), table="")

    # test build_column with alias and column as str expr
    assert build_column(this="col1", alias="col1_aliased") == exp.Alias(
        this=exp.Column(this="col1", table=""), alias=exp.Identifier(this="col1_aliased", quoted=False)
    )

    # test build_column with alias and column as exp.Column expr
    assert build_column(
        this=exp.Column(this=exp.Identifier(this="col1", quoted=False), table=""), alias="col1_aliased"
    ) == exp.Alias(
        this=exp.Column(this=exp.Identifier(this="col1", quoted=False), table=""),
        alias=exp.Identifier(this="col1_aliased", quoted=False),
    )

    # with table name
    result = build_column(this="test_column", alias="test_alias", table_name="test_table")
    assert str(result) == "test_table.test_column AS test_alias"

    # with table name
    result = build_column(this="test_column", alias="test_alias", table_name="test_table", quoted=True)
    assert str(result) == "test_table.test_column AS \"test_alias\""


def test_build_literal():
    actual = build_literal(this="abc")
    expected = exp.Literal(this="abc", is_string=True)

    assert actual == expected


def test_build_literal_cast_renders_in_source_dialect():
    """With a source dialect, a declared type renders in that engine's own nomenclature.

    Redshift external/Spectrum tables report Spark/Glue "string", which Redshift rejects verbatim;
    it must become VARCHAR(MAX) so the literal CAST pushed down to Redshift is valid SQL.
    """
    literal = build_literal(
        this="sample_key", alias="key_col", cast="string", cast_dialect=get_dialect("redshift"), quoted=True
    )

    assert literal.sql(dialect="redshift") == 'CAST(\'sample_key\' AS VARCHAR(MAX)) AS "key_col"'


def test_build_literal_cast_without_dialect_is_verbatim():
    """Without a source dialect -- every non-Redshift caller -- the cast stays verbatim, so the
    Redshift fix leaves other sources byte-for-byte unchanged."""
    literal = build_literal(this="sample_key", alias="key_col", cast="string", quoted=True)

    assert literal.sql(dialect="redshift") == 'CAST(\'sample_key\' AS string) AS "key_col"'


def test_build_literal_cast_unparseable_type_falls_back_verbatim():
    """An unparseable declared type must not raise; it falls back to the verbatim form.

    Covers both sqlglot error classes: ParseError (unknown type) and TokenError (a type string
    with a stray quote/comment token) -- both subclass SqlglotError and must fall back, not raise.
    """
    parse_error = build_literal(
        this="sample_key", alias="key_col", cast="not a type", cast_dialect=get_dialect("redshift"), quoted=True
    )
    assert parse_error.sql(dialect="redshift") == 'CAST(\'sample_key\' AS not a type) AS "key_col"'

    # A quote in the type string triggers sqlglot TokenError (not ParseError); must not raise.
    token_error = build_literal(
        this="sample_key", alias="key_col", cast="var'char", cast_dialect=get_dialect("redshift"), quoted=True
    )
    assert token_error.sql(dialect="redshift").startswith("CAST('sample_key' AS ")


def test_sha2(expr):
    assert sha2(expr, num_bits="256").sql() == "SELECT SHA2(col1, 256) FROM DUAL"
    assert (
        sha2(Column(this="CONCAT(col1,col2,col3)"), num_bits="256", is_expr=True).sql()
        == "SHA2(CONCAT(col1,col2,col3), 256)"
    )


def test_md5(expr):
    assert md5(expr).sql() == "SELECT MD5(col1) FROM DUAL"
    assert md5(Column(this="CONCAT(col1,col2,col3)"), is_expr=True).sql() == "MD5(CONCAT(col1,col2,col3))"


def test_concat():
    exprs = [exp.Expression(this="col1"), exp.Expression(this="col2")]
    result = concat(exprs)
    # concat() now returns exp.DPipe to use dialect-native concatenation (|| or +)
    expected = exp.DPipe(this=exp.Expression(this="col1"), expression=exp.Expression(this="col2"))
    assert result == expected


def test_lower(expr):
    assert lower(expr).sql() == "SELECT LOWER(col1) FROM DUAL"
    assert lower(Column(this="CONCAT(col1,col2,col3)"), is_expr=True).sql() == "LOWER(CONCAT(col1,col2,col3))"


def test_get_hash_transform():
    assert isinstance(get_hash_transform(get_dialect("snowflake"), "source"), list) is True

    with pytest.raises(ValueError):
        get_hash_transform(get_dialect("trino"), "source")

    with pytest.raises(ValueError):
        get_hash_transform(get_dialect("snowflake"), "sourc")


def test_build_from_clause():
    # with table alias
    result = build_from_clause("test_table", "test_alias")
    assert str(result) == "FROM test_table AS test_alias"
    assert isinstance(result, exp.From)
    assert result.this.this.this == "test_table"
    assert result.this.alias == "test_alias"

    # without table alias
    result = build_from_clause("test_table")
    assert str(result) == "FROM test_table"


def test_build_join_clause():
    # with table alias
    result = build_join_clause(
        table_name="test_table",
        join_columns=["test_column"],
        source_table_alias="source",
        target_table_alias="test_alias",
    )
    assert str(result) == (
        "INNER JOIN test_table AS test_alias ON source.test_column IS NOT DISTINCT FROM test_alias.test_column"
    )
    assert isinstance(result, exp.Join)
    assert result.this.this.this == "test_table"
    assert result.this.alias == "test_alias"

    # without table alias
    result = build_join_clause("test_table", ["test_column"])
    assert str(result) == "INNER JOIN test_table ON test_column IS NOT DISTINCT FROM test_column"


def test_build_sub():
    # with table name
    result = build_sub("left_column", "right_column", "left_table", "right_table")
    assert str(result) == "left_table.left_column - right_table.right_column"
    assert isinstance(result, exp.Sub)
    assert result.this.this.this == "left_column"
    assert result.this.table == "left_table"
    assert result.expression.this.this == "right_column"
    assert result.expression.table == "right_table"

    # without table name
    result = build_sub("left_column", "right_column")
    assert str(result) == "left_column - right_column"


def test_build_where_clause():
    # or condition
    where_clause = [
        exp.EQ(
            this=exp.Column(this="test_column", table="test_table"), expression=exp.Literal(this='1', is_string=False)
        )
    ]
    result = build_where_clause(where_clause)
    assert str(result) == "(1 = 1 OR 1 = 1) OR test_table.test_column = 1"
    assert isinstance(result, exp.Or)

    # and condition
    where_clause = [
        exp.EQ(
            this=exp.Column(this="test_column", table="test_table"), expression=exp.Literal(this='1', is_string=False)
        )
    ]
    result = build_where_clause(where_clause, "and")
    assert str(result) == "(1 = 1 AND 1 = 1) AND test_table.test_column = 1"
    assert isinstance(result, exp.And)


def test_build_if():
    # with true and false
    result = build_if(
        this=exp.EQ(
            this=exp.Column(this="test_column", table="test_table"), expression=exp.Literal(this='1', is_string=False)
        ),
        true=exp.Literal(this='1', is_string=False),
        false=exp.Literal(this='0', is_string=False),
    )
    assert str(result) == "CASE WHEN test_table.test_column = 1 THEN 1 ELSE 0 END"
    assert isinstance(result, exp.If)

    # without false
    result = build_if(
        this=exp.EQ(
            this=exp.Column(this="test_column", table="test_table"), expression=exp.Literal(this='1', is_string=False)
        ),
        true=exp.Literal(this='1', is_string=False),
    )
    assert str(result) == "CASE WHEN test_table.test_column = 1 THEN 1 END"


def test_build_between():
    result = build_between(
        this=exp.Column(this="test_column", table="test_table"),
        low=exp.Literal(this='1', is_string=False),
        high=exp.Literal(this='2', is_string=False),
    )
    assert str(result) == "test_table.test_column BETWEEN 1 AND 2"
    assert isinstance(result, exp.Between)
