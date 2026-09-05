"""Unit tests for the Teradata streaming Arrow-batch construction.

The pipeline appends streamed batches positionally into a DDL-pre-created DuckDB table, so every
batch a query produces must carry the *same* Arrow schema — even a batch whose values for some
column are all NULL. These tests pin that invariant without needing a live Teradata connection.
"""

import datetime
import decimal

import pyarrow as pa

from databricks.labs.lakebridge.connections.database_manager import (
    _arrow_schema_from_description,
    _rows_to_arrow_table,
)

# teradatasql reports type_code as the Python type it returns values as.
_DESCRIPTION = [
    ("name", str, None, None, None, None, None),
    ("count", int, None, None, None, None, None),
    ("cpu", float, None, None, None, None, None),
    ("amount", decimal.Decimal, None, None, None, None, None),
    ("collected_at", datetime.datetime, None, None, None, None, None),
    ("as_of", datetime.date, None, None, None, None, None),
]


def test_schema_maps_python_type_codes_to_arrow() -> None:
    schema = _arrow_schema_from_description(_DESCRIPTION)
    assert schema.names == ["name", "count", "cpu", "amount", "collected_at", "as_of"]
    assert schema.types == [
        pa.string(),
        pa.int64(),
        pa.float64(),
        pa.string(),  # Decimal columns are carried as exact text, not float64 (avoids rounding)
        pa.timestamp("us"),
        pa.date32(),
    ]


def test_batches_share_one_schema_even_when_a_column_is_all_null() -> None:
    schema = _arrow_schema_from_description(_DESCRIPTION)
    typed = _rows_to_arrow_table(
        [("db1", 5, 1.5, decimal.Decimal("2.50"), datetime.datetime(2026, 1, 1, 12), datetime.date(2026, 1, 1))],
        schema,
    )
    # A later batch where 'cpu' and 'amount' are entirely NULL must not infer a different type.
    all_null = _rows_to_arrow_table(
        [("db2", 9, None, None, datetime.datetime(2026, 2, 2, 6), datetime.date(2026, 2, 2))],
        schema,
    )
    assert typed.schema == all_null.schema == schema


def test_values_are_coerced_to_column_types() -> None:
    schema = _arrow_schema_from_description(_DESCRIPTION)
    table = _rows_to_arrow_table(
        [("db1", 5, 1.5, decimal.Decimal("2.50"), datetime.datetime(2026, 1, 1, 12), datetime.date(2026, 1, 1))],
        schema,
    )
    row = table.to_pylist()[0]
    assert row["name"] == "db1"
    assert row["count"] == 5
    assert row["cpu"] == 1.5
    assert row["amount"] == "2.50"  # Decimal -> exact text
    assert row["collected_at"] == datetime.datetime(2026, 1, 1, 12)
    assert row["as_of"] == datetime.date(2026, 1, 1)


def test_unknown_type_code_falls_back_to_string() -> None:
    schema = _arrow_schema_from_description([("blob", object, None, None, None, None, None)])
    assert schema.types == [pa.string()]
    table = _rows_to_arrow_table([(123,)], schema)
    assert table.to_pylist() == [{"blob": "123"}]  # non-str values stringified for the fallback


def test_large_decimal_carried_exactly_without_float_rounding() -> None:
    # A counter beyond 2**53 would lose its low-order digits if coerced through float().
    big = decimal.Decimal("9007199254740993")  # 2**53 + 1, not representable as float64
    schema = _arrow_schema_from_description([("total_io", decimal.Decimal, None, None, None, None, None)])
    table = _rows_to_arrow_table([(big,)], schema)
    assert table.to_pylist() == [{"total_io": "9007199254740993"}]


def test_tz_aware_datetime_normalized_to_naive_utc() -> None:
    # TIMESTAMP WITH TIME ZONE comes back tz-aware; the schema is tz-naive, so it must be converted
    # to UTC and stripped of tzinfo rather than raising inside pa.array.
    aware = datetime.datetime(2026, 1, 1, 12, tzinfo=datetime.timezone(datetime.timedelta(hours=5, minutes=30)))
    schema = _arrow_schema_from_description([("collected_at", datetime.datetime, None, None, None, None, None)])
    table = _rows_to_arrow_table([(aware,)], schema)
    assert table.schema.types == [pa.timestamp("us")]
    assert table.to_pylist() == [{"collected_at": datetime.datetime(2026, 1, 1, 6, 30)}]  # 12:00 +05:30 -> 06:30 UTC
