"""Unit tests for OPT-A-1 — the typed schema layer behind ``_fingerprint_metrics_struct_sql``.

These tests pin the new contract introduced on 2026-05-09:
  - The ``_FP_METRICS_STRUCT_FIELDS`` tuple is the single source of truth.
  - Every metadata attribute is covered exactly once.
  - Every entry's ``sql_type`` is one of the four supported types.
  - ``_render_fp_metrics_value`` is type-checked at the boundary; an unsupported
    sql_type raises rather than silently falling through.
  - The dataclass field order matches the schema declaration so Delta's positional
    struct resolution (saveAsTable) cannot drift.

Pairs with ``test_recon_capture_fingerprint.py`` (which pins the rendered SQL string
shape — bit-exact preservation across the typed-schema rewrite). The two files share
an intentional duplication of the field-order tuple so a reorder breaks both tests
from different angles ("declaration order" here vs. "SQL emission order" there);
silencing pylint's similarity checker here documents that as deliberate.
"""

# pylint: disable=duplicate-code

from __future__ import annotations

import dataclasses

import pytest

from databricks.labs.lakebridge.reconcile.fingerprint.metadata import FingerprintRunMetadata
from databricks.labs.lakebridge.reconcile.recon_capture import (
    _FP_METRICS_STRUCT_FIELDS,
    _render_fp_metrics_value,
)

_ALLOWED_SQL_TYPES = {"bool", "bigint", "bigint_or_null", "string_or_null"}


def test_schema_declares_thirteen_fields_in_dataclass_order():
    """The schema tuple must enumerate every persisted field exactly once, in the order
    Delta expects on positional struct resolution. Any drift is an MR-blocker.
    """
    assert len(_FP_METRICS_STRUCT_FIELDS) == 13, (
        f"Expected 13 fp_metrics fields, got {len(_FP_METRICS_STRUCT_FIELDS)}. "
        "If you added a field, update this assertion AND verify the dataclass declaration."
    )

    schema_attrs = [attr for (_sql_field, attr, _sql_type) in _FP_METRICS_STRUCT_FIELDS]
    dataclass_attrs = [f.name for f in dataclasses.fields(FingerprintRunMetadata)]

    # Every schema attribute must exist on the dataclass — otherwise getattr() at render
    # time would raise AttributeError on a code path with full unit coverage today.
    for attr in schema_attrs:
        assert attr in dataclass_attrs, (
            f"Schema declares attribute {attr!r} that is NOT on FingerprintRunMetadata. "
            "Either remove from schema or add to the dataclass."
        )


def test_schema_field_names_unique():
    """A duplicate sql_field_name would render twice in the same named_struct, producing
    invalid SQL on the persisted row. Pin uniqueness here so a copy-paste edit is caught
    at unit-test time, not at recon runtime.
    """
    sql_field_names = [sql_field for (sql_field, _attr, _sql_type) in _FP_METRICS_STRUCT_FIELDS]
    assert len(sql_field_names) == len(
        set(sql_field_names)
    ), f"Duplicate sql_field_name in _FP_METRICS_STRUCT_FIELDS: {sql_field_names!r}"

    schema_attrs = [attr for (_sql_field, attr, _sql_type) in _FP_METRICS_STRUCT_FIELDS]
    assert len(schema_attrs) == len(
        set(schema_attrs)
    ), f"Duplicate dataclass attribute in _FP_METRICS_STRUCT_FIELDS: {schema_attrs!r}"


def test_schema_only_uses_allowed_sql_types():
    """The renderer raises on an unknown sql_type. This test is the static check —
    if a developer adds a field with sql_type='int' instead of 'bigint', the test fails
    at unit-test time before the bad type reaches `spark.sql(...)`.
    """
    for sql_field, attr, sql_type in _FP_METRICS_STRUCT_FIELDS:
        assert sql_type in _ALLOWED_SQL_TYPES, (
            f"Schema entry {sql_field!r} (attr={attr!r}) declares unsupported sql_type "
            f"{sql_type!r}. Allowed: {sorted(_ALLOWED_SQL_TYPES)!r}."
        )


def test_render_bool_emits_lowercase_sql_literal():
    assert _render_fp_metrics_value(True, "bool") == "true"
    assert _render_fp_metrics_value(False, "bool") == "false"
    # Truthy non-bool coerces — guards against a future int-typed attribute being
    # accidentally rendered as 'bool' and producing 'True' (capitalised, invalid SQL).
    assert _render_fp_metrics_value(1, "bool") == "true"
    assert _render_fp_metrics_value(0, "bool") == "false"


def test_render_bigint_emits_explicit_cast():
    """Without explicit cast, Spark infers int from small literals and later writes
    with larger counts force a slow column-type rewrite. Cast is mandatory.
    """
    assert _render_fp_metrics_value(0, "bigint") == "cast(0 as bigint)"
    assert _render_fp_metrics_value(2_097_152, "bigint") == "cast(2097152 as bigint)"


def test_render_bigint_or_null_handles_none():
    assert _render_fp_metrics_value(None, "bigint_or_null") == "NULL"
    assert _render_fp_metrics_value(0, "bigint_or_null") == "cast(0 as bigint)"
    assert _render_fp_metrics_value(100_000_000, "bigint_or_null") == "cast(100000000 as bigint)"


def test_render_string_or_null_handles_none_and_quotes_scrub():
    assert _render_fp_metrics_value(None, "string_or_null") == "NULL"
    assert _render_fp_metrics_value("MATCH", "string_or_null") == "'MATCH'"
    # Defense-in-depth: embedded quotes scrubbed (mirrors exception_message handling
    # elsewhere in recon_capture).
    assert _render_fp_metrics_value("bad'reason\"here", "string_or_null") == "'badreasonhere'"


def test_render_unknown_sql_type_raises():
    """An unknown sql_type must raise — the renderer never silently falls through to
    str(value), which would produce un-cast/un-quoted output that breaks the SQL.
    """
    with pytest.raises(ValueError, match="Unsupported sql_type"):
        _render_fp_metrics_value(42, "int")
    with pytest.raises(ValueError, match="Unsupported sql_type"):
        _render_fp_metrics_value("foo", "varchar")
    with pytest.raises(ValueError, match="Unsupported sql_type"):
        _render_fp_metrics_value(None, "")


def test_render_bigint_or_null_rejects_non_none_non_int():
    """``FingerprintRunMetadata`` types these fields as ``int | None``; a stringy value
    is a contract violation and must fail loudly. The ``isinstance`` assertion is also
    what narrows ``value: object`` for mypy in ``_render_fp_metrics_value``.
    """
    with pytest.raises(AssertionError, match="bigint_or_null field expected"):
        _render_fp_metrics_value("not-a-number", "bigint_or_null")


def test_render_bigint_rejects_stringy_value():
    """Companion of the bigint_or_null contract — non-nullable bigint also rejects strings."""
    with pytest.raises(AssertionError, match="bigint field expected"):
        _render_fp_metrics_value("100", "bigint")


def test_struct_sql_field_order_pinned_by_schema():
    """Belt-and-braces: pin the exact order so a field reorder in
    ``_FP_METRICS_STRUCT_FIELDS`` shows up as an MR diff line, not a silent runtime
    behaviour change.
    """
    expected_order = (
        "eligible",
        "ineligibility_reason",
        "verdict",
        "elapsed_ms",
        "solved_count",
        "unsolved_sb_count",
        "total_mismatched_sbs",
        "fallback_to_full_pipeline",
        "sub_bucket_count",
        "bucket_count",
        "target_row_count",
        "row_count_source",
        "fetch_path",
    )
    actual_order = tuple(sql_field for (sql_field, _attr, _t) in _FP_METRICS_STRUCT_FIELDS)
    assert actual_order == expected_order, (
        "Field order drifted in _FP_METRICS_STRUCT_FIELDS. Reordering is a Delta "
        "schema-positional break for existing recon_metrics rows. Confirm intent."
    )


def test_render_helper_is_module_level_not_method():
    """The renderer is module-level by design — keeping it out of the class makes
    OPT-A-1's safety boundary independent of any future ReconCapture refactor that
    might break inheritance / decorator behaviour. Pin the import path.
    """
    from databricks.labs.lakebridge.reconcile import recon_capture as rc  # noqa: PLC0415

    assert callable(rc._render_fp_metrics_value)
    # Not a method — calling without `self` must work.
    assert rc._render_fp_metrics_value(True, "bool") == "true"


def test_schema_attrs_match_dataclass_fields_one_to_one():
    """Every dataclass field must appear in the schema (otherwise a new metadata
    field would silently fail to persist) AND every schema entry must reference a
    real dataclass field (otherwise getattr() raises at render time).
    """
    schema_attrs = {attr for (_sql_field, attr, _t) in _FP_METRICS_STRUCT_FIELDS}
    dataclass_attrs = {f.name for f in dataclasses.fields(FingerprintRunMetadata)}

    # If a dataclass field is added without a schema entry, this fails — forcing the
    # author to deliberately decide whether the new field is persisted.
    missing_in_schema = dataclass_attrs - schema_attrs
    assert not missing_in_schema, (
        f"Dataclass attributes missing from _FP_METRICS_STRUCT_FIELDS: {sorted(missing_in_schema)!r}. "
        "Either add to the schema, or document why the field is intentionally non-persistent."
    )

    extra_in_schema = schema_attrs - dataclass_attrs
    assert not extra_in_schema, (
        f"Schema references non-existent dataclass attributes: {sorted(extra_in_schema)!r}. "
        "Remove from schema or add to FingerprintRunMetadata."
    )
