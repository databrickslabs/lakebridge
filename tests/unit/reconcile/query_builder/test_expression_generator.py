from datetime import datetime, timezone

import pytest
from sqlglot import Dialect, parse_one
from sqlglot import expressions as exp
from sqlglot.expressions import Column

from databricks.labs.lakebridge.reconcile.query_builder.column_transformer import (
    get_transform_for_type,
)
from databricks.labs.lakebridge.reconcile.query_builder.expression_generator import (
    array_sort,
    array_to_string,
    build_between,
    build_column,
    build_column_no_alias,
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
    transform_expression,
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


# ---------------------------------------------------------------------------
# Dialect-aligned TIMESTAMP/TIMESTAMPTZ serialization
# ---------------------------------------------------------------------------
#
# Regression for the ``expression_generator.py`` mapping bug where Redshift's
# source-side hash input emits a 26-char microsecond-precision string
# (``2023-11-18 18:38:07.000000``) but Databricks' target-side default cast
# emits a 19-char string (``2023-11-18 18:38:07``). The 7-byte drift makes the
# per-row SHA2 hashes disagree for *every* logically-identical TIMESTAMP/
# TIMESTAMPTZ row in any Redshift -> Databricks reconcile -- in normal mode
# this is whole-table noise; in fingerprint mode it surfaces only on the
# small set of rows that Stage-2's surgical fetch over-pulls due to 32-bit
# ``rh1`` sub-bucket collisions (~5 per 1M rows / 10K culprits, statistical).
#
# The fix adds explicit Databricks handlers so the target side emits the same
# ``yyyy-MM-dd HH:mm:ss.SSSSSS`` shape as Redshift's
# ``TO_CHAR(ts, 'YYYY-MM-DD HH24:MI:SS.US')``.


def _handlers_for(dialect_name: str, type_value: str) -> list:
    """The transform handlers ``dialect_name`` applies to a column of ``type_value``,
    resolved through the public ``get_transform_for_type`` lookup — the same entry point
    the row-hash path uses. ``counterpart=None`` keeps the counterpart-gated pins, so this
    returns the dialect's own handler without reaching into the private transform table."""
    return get_transform_for_type(type_value, get_dialect(dialect_name), None)


def _apply_handler(handler_partial, col_name: str = "ts_col") -> str:
    """Run a single per-type transform partial against a column expression and return the
    rendered SQL. Mirrors how ``HashQueryBuilder._apply_transform`` threads partials over
    the projected expression."""
    rendered = handler_partial(exp.Column(this=col_name))
    return rendered.sql(dialect="databricks")


_EXPECTED_REDSHIFT_TS_SQL = "COALESCE(TO_CHAR(ts_col, 'YYYY-MM-DD HH24:MI:SS.US'), '_null_recon_')"
_EXPECTED_DATABRICKS_TS_SQL = "COALESCE(DATE_FORMAT(ts_col, 'yyyy-MM-dd HH:mm:ss.SSSSSS'), '_null_recon_')"
# Redshift pins TIMESTAMPTZ to UTC via ``AT TIME ZONE 'UTC'`` so its render is
# independent of the Redshift session ``TIMEZONE``. The Databricks side renders via
# ``DATE_FORMAT`` in ``spark.sql.session.timeZone``, so it matches only when the
# reconcile cluster session is UTC (the Databricks default) — sqlglot's Databricks
# dialect cannot distinguish naive TIMESTAMP from TIMESTAMPTZ, so a per-type SQL pin
# is not expressible there.
_EXPECTED_REDSHIFT_TSTZ_SQL = "COALESCE(TO_CHAR(ts_col AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS.US'), '_null_recon_')"


def test_databricks_timestamp_handler_emits_exact_microsecond_sql():
    """Pin the exact rendered SQL for the Databricks TIMESTAMP handler.

    A substring match on ``HH:mm:ss.SSSSSS`` would tolerate a typo in the
    surrounding ``COALESCE`` / sentinel pieces; equality on the whole string
    is the strongest unit-level guard.
    """
    handlers = _handlers_for("databricks", exp.DataType.Type.TIMESTAMP.value)
    assert len(handlers) == 1, "expected exactly one TIMESTAMP handler for databricks"
    assert _apply_handler(handlers[0]) == _EXPECTED_DATABRICKS_TS_SQL


def test_databricks_timestamptz_handler_emits_exact_microsecond_sql():
    """The Databricks handler renders plain ``DATE_FORMAT`` (no per-type TZ pin —
    sqlglot maps naive TIMESTAMP and TIMESTAMPTZ to this single entry). UTC
    determinism relies on the reconcile cluster session being UTC (the Databricks
    default), not on anything rendered here."""
    handlers = _handlers_for("databricks", exp.DataType.Type.TIMESTAMPTZ.value)
    assert len(handlers) == 1, "expected exactly one TIMESTAMPTZ handler for databricks"
    assert _apply_handler(handlers[0]) == _EXPECTED_DATABRICKS_TS_SQL


def test_redshift_timestamp_handler_emits_exact_microsecond_sql():
    rs_handlers = _handlers_for("redshift", exp.DataType.Type.TIMESTAMP.value)
    assert len(rs_handlers) == 1, "expected exactly one TIMESTAMP handler for redshift"
    rs_rendered = rs_handlers[0](exp.Column(this="ts_col")).sql(dialect="redshift")
    assert rs_rendered == _EXPECTED_REDSHIFT_TS_SQL


def test_redshift_timestamptz_handler_pins_utc():
    """TIMESTAMPTZ pins to UTC via ``AT TIME ZONE 'UTC'`` so the render is
    independent of the Redshift session ``TIMEZONE`` setting."""
    rs_handlers = _handlers_for("redshift", exp.DataType.Type.TIMESTAMPTZ.value)
    assert len(rs_handlers) == 1, "expected exactly one TIMESTAMPTZ handler for redshift"
    rs_rendered = rs_handlers[0](exp.Column(this="ts_col")).sql(dialect="redshift")
    assert rs_rendered == _EXPECTED_REDSHIFT_TSTZ_SQL


def test_databricks_and_redshift_timestamp_format_strings_produce_identical_bytes():
    """The two handlers must emit byte-identical format output for the same
    instant. We can't execute SQL in a unit test, so we render the canonical
    reference timestamp through Python's ``strftime`` with the equivalent
    format string the docs guarantee for each engine and assert equality on
    the produced wall-clock string.

    Redshift's ``YYYY-MM-DD HH24:MI:SS.US`` and Spark's
    ``yyyy-MM-dd HH:mm:ss.SSSSSS`` are documented to produce 6-digit
    microsecond precision, so the canonical Python equivalent is
    ``%Y-%m-%d %H:%M:%S.%f``. This test guards the on-the-wire byte equality
    that the per-row SHA2 hash relies on.
    """
    reference = datetime(2025, 1, 15, 12, 34, 56, 789012, tzinfo=timezone.utc)
    canonical = reference.strftime("%Y-%m-%d %H:%M:%S.%f")
    assert canonical == "2025-01-15 12:34:56.789012"

    rs_handlers = _handlers_for("redshift", exp.DataType.Type.TIMESTAMPTZ.value)
    db_handlers = _handlers_for("databricks", exp.DataType.Type.TIMESTAMPTZ.value)
    rs_rendered = rs_handlers[0](exp.Column(this="ts_col")).sql(dialect="redshift")
    db_rendered = db_handlers[0](exp.Column(this="ts_col")).sql(dialect="databricks")

    assert "'YYYY-MM-DD HH24:MI:SS.US'" in rs_rendered
    assert "'yyyy-MM-dd HH:mm:ss.SSSSSS'" in db_rendered


def _render_double(source: str, counterpart: str | None) -> str:
    src = get_dialect(source)
    other = get_dialect(counterpart) if counterpart else None
    node = build_column_no_alias(this="carat")
    return transform_expression(node, get_transform_for_type("double", src, other)).sql(dialect=src)


def _render_type(datatype: str, source: str, counterpart: str | None) -> str:
    src = get_dialect(source)
    other = get_dialect(counterpart) if counterpart else None
    node = build_column_no_alias(this="ts_col")
    return transform_expression(node, get_transform_for_type(datatype, src, other)).sql(dialect=src)


def test_double_pins_only_when_counterpart_also_pins():
    """Regression: the DOUBLE -> DECIMAL(38,10) pin is only byte-identical when *both*
    engines pin. Redshift <-> Databricks both pin, so the pin is emitted. A non-pinning
    counterpart (BigQuery/Snowflake/Oracle/TSQL source into a Databricks target) must
    fall back to the universal default on the Databricks side -- otherwise every DOUBLE
    row false-mismatches and, on BigQuery, the follow-up mismatch-sampling query hits a
    ``CAST(... AS string(N))`` parse error and errors the whole reconcile.
    """
    # Redshift <-> Databricks: both sides keep the DECIMAL(38,10) pin.
    assert "DECIMAL(38,10)" in _render_double("databricks", "redshift")
    assert "DECIMAL(38,10)" in _render_double("redshift", "databricks")

    # Databricks target paired with a non-pinning source: no pin (matches main).
    assert "DECIMAL(38,10)" not in _render_double("databricks", "bigquery")
    assert _render_double("databricks", "bigquery") == "COALESCE(TRIM(carat), '_null_recon_')"

    # Unknown counterpart (None, e.g. the Redshift-only fingerprint path) keeps the pin.
    assert "DECIMAL(38,10)" in _render_double("databricks", None)


def test_double_handlers_guard_decimal_overflow():
    """A finite double whose magnitude >= 1e28 overflows ``DECIMAL(38,10)`` (28 integer
    digits): Redshift raises "numeric field overflow" and aborts the whole recon while
    Spark (non-ANSI) returns NULL. Both DOUBLE handlers must route such values (like
    NaN/Infinity) through the native string cast instead of the DECIMAL pin so the
    reconcile does not crash. Regression for the overflow gap in the pinned handler."""
    db_rendered = _render_double("databricks", "redshift")
    rs_rendered = _render_double("redshift", "databricks")
    # The overflow magnitude check is present on both sides.
    assert "ABS(ts_col)" not in db_rendered  # sanity: column is `carat` here
    assert ">= 1e28" in db_rendered
    assert ">= 1e28" in rs_rendered
    # It shares the special-value (native cast) branch, and the DECIMAL pin still exists
    # for in-range values.
    assert "DECIMAL(38,10)" in db_rendered
    assert "DECIMAL(38,10)" in rs_rendered


def test_redshift_double_float_detect_nan_via_varchar_cast():
    """CF-2 regression: a live Redshift probe showed a *column* ``NaN`` is NOT matched by
    ``NaN IN (CAST('NaN' AS DOUBLE PRECISION), ...)`` -- even though ``NaN = NaN`` is TRUE
    and ±Inf ARE matched by that IN. So the old float-``IN`` guard silently missed NaN,
    letting it fall into the ELSE ``CAST(... AS DECIMAL(38,10))`` and abort the whole
    reconcile with SQLSTATE 8001 "NaN input". NaN/±Inf must be detected via the VARCHAR cast
    (``CAST({0} AS VARCHAR) IN ('NaN','Infinity','-Infinity')``), which renders all three
    reliably. With a correct guard the ELSE only ever sees finite, in-range values, so it
    feeds the raw column straight to the DECIMAL pin -- no sanitize/clamp (proven live: the
    plain-ELSE serializer runs clean on a real NaN-bearing column). A ``{0} <> {0}`` check
    would be WRONG here (``NaN <> NaN`` is FALSE on Redshift). Databricks is unchanged
    (``ISNAN`` already detects NaN)."""
    for dtype in ("double", "real", "float4"):
        rendered = _render_type(dtype, "redshift", "databricks")
        # NaN/±Inf are detected by casting to VARCHAR -- never by the float IN (misses a
        # column NaN) and never by `<>` self (``NaN <> NaN`` is FALSE on Redshift).
        assert "CAST(ts_col AS VARCHAR) IN (" in rendered, dtype
        assert "'NaN'" in rendered and "'Infinity'" in rendered and "'-Infinity'" in rendered, dtype
        assert "IN (CAST('NaN' AS DOUBLE PRECISION)" not in rendered, dtype
        assert "<>" not in rendered, dtype
        # The ELSE feeds the raw column straight to the DECIMAL pin -- no sanitize (``THEN 0``).
        assert "CAST(CAST(ts_col AS DECIMAL(38,10)) AS VARCHAR)" in rendered, dtype
        assert "THEN 0 ELSE" not in rendered, dtype
        # In-range values are still normalized to the fixed-scale decimal string, and finite
        # magnitude >= 1e28 (overflows DECIMAL(38,10)) still routes to the native cast.
        assert "DECIMAL(38,10)" in rendered, dtype
        assert ">= 1e28" in rendered, dtype


def test_float_pins_like_double_only_when_counterpart_also_pins():
    """Regression (F1): Redshift ``real``/``float4`` (single precision) parses to FLOAT and
    previously had no handler, falling to the universal ``TRIM`` default — the same
    full-precision-vs-shortest-round-trip divergence that false-mismatched every DOUBLE row
    before its pin. FLOAT must pin to the SAME ``DECIMAL(38,10)`` scale as DOUBLE (so a
    float/double column pair stays byte-identical, since sqlglot resolves Redshift ``float``
    to DOUBLE but Databricks ``float`` to FLOAT), and stay counterpart-gated so a non-Redshift
    source is unaffected."""
    for dtype in ("real", "float4"):
        # Redshift <-> Databricks: pinned identically to DOUBLE.
        assert "DECIMAL(38,10)" in _render_type(dtype, "redshift", "databricks"), dtype
        assert "DECIMAL(38,10)" in _render_type(dtype, "databricks", "redshift"), dtype
        # Non-pinning counterpart: fall back to the universal default (no behaviour change).
        assert _render_type(dtype, "databricks", "snowflake") == "COALESCE(TRIM(ts_col), '_null_recon_')", dtype
        # Unknown counterpart (None, e.g. the Redshift-only fingerprint path) keeps the pin.
        assert "DECIMAL(38,10)" in _render_type(dtype, "databricks", None), dtype


def test_timestamp_pins_only_when_counterpart_also_pins():
    """The Databricks TIMESTAMP/TIMESTAMPTZ microsecond pin (``DATE_FORMAT`` with
    ``.SSSSSS``) is only byte-identical when the counterpart also pins (Redshift). Against
    a non-pinning source it must fall back to the universal default so this fix does NOT
    change target-side serialization for existing Snowflake/Oracle/TSQL/BigQuery ->
    Databricks reconciles (which would otherwise false-mismatch every timestamp row).
    Mirrors the DOUBLE gating."""
    # Databricks target paired with a Redshift source: keep the microsecond pin.
    assert "DATE_FORMAT" in _render_type("timestamp", "databricks", "redshift")

    # Databricks target paired with a non-pinning source: fall back to universal default.
    assert _render_type("timestamp", "databricks", "snowflake") == "COALESCE(TRIM(ts_col), '_null_recon_')"
    assert _render_type("timestamp", "databricks", "oracle") == "COALESCE(TRIM(ts_col), '_null_recon_')"

    # Unknown counterpart (None, e.g. the Redshift-only fingerprint path) keeps the pin.
    assert "DATE_FORMAT" in _render_type("timestamp", "databricks", None)


def test_redshift_boolean_handler_emits_exact_case_when_sql():
    """Redshift rejects every ``CAST(boolean AS VARCHAR/TEXT)`` form, and the
    universal default ``TRIM(...)`` becomes ``btrim(boolean)`` which is
    function-not-found. The custom handler must emit lowercase ``'true'`` /
    ``'false'`` literals so the bytes match Spark's
    ``cast(boolean AS string)`` output and per-row hashes stay aligned.
    """
    handlers = _handlers_for("redshift", exp.DataType.Type.BOOLEAN.value)
    assert len(handlers) == 1, "expected exactly one BOOLEAN handler for redshift"
    rendered = handlers[0](exp.Column(this="bool_col")).sql(dialect="redshift")
    assert rendered == (
        "COALESCE(CASE WHEN bool_col THEN 'true' WHEN NOT bool_col THEN 'false' ELSE NULL END, " "'_null_recon_')"
    )


def test_unmapped_dialect_falls_back_to_universal_serialization():
    """A source dialect with no ``_DATATYPE_TRANSFORM_MAPPING`` entry serializes through the
    universal default via ``_dialect_key``'s fallback, so an unsupported engine still
    reconciles rather than raising. Covers both fallback arms: a dialect whose sqlglot
    synonyms exist but carry no mapping entry (Postgres family -> ``keys[0]``), and one that
    resolves to no registered synonym at all (-> ``universal``)."""
    node = build_column_no_alias(this="c")
    # Postgres family: sqlglot collapses netezza/postgresql/vertica onto one Dialect, none of
    # which is a mapping key, so _dialect_key returns the first synonym and the lookup falls
    # through to the universal default.
    postgres = get_dialect("postgresql")
    assert (
        transform_expression(node, get_transform_for_type("integer", postgres, None)).sql(dialect=postgres)
        == "COALESCE(TRIM(c), '_null_recon_')"
    )
    # A dialect that matches no registered synonym at all -> _dialect_key returns "universal".
    duckdb = Dialect.get_or_raise("duckdb")
    assert (
        transform_expression(node, get_transform_for_type("integer", duckdb, None)).sql(dialect=duckdb)
        == "COALESCE(TRIM(c), '_null_recon_')"
    )
