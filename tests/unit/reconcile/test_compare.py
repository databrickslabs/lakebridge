# Intentionally exercising the internal column-selection helper directly: it is pure string
# logic (no SparkSession), so a unit test pins the key-vs-flag disambiguation without a cluster.
from databricks.labs.lakebridge.reconcile.compare import (  # pylint: disable=import-private-name
    _row_mismatch_flag_columns,
)


def test_row_mismatch_flag_columns_picks_only_genuine_match_triples():
    """A genuine ``<col>_match`` flag is emitted by ``_get_mismatch_df`` alongside its
    ``_base``/``_compare`` siblings. Only those should be returned."""
    columns = [
        "id",  # key column, projected unsuffixed
        "name_base",
        "name_compare",
        "name_match",  # genuine flag
        "acctbal_base",
        "acctbal_compare",
        "acctbal_match",  # genuine flag
    ]
    assert _row_mismatch_flag_columns(columns) == ["name_match", "acctbal_match"]


def test_row_mismatch_flag_columns_ignores_key_column_named_like_a_flag():
    """Regression: a join key literally named ``*_match`` is projected unsuffixed (no
    ``_base``/``_compare`` siblings), so it must NOT be treated as a boolean match flag —
    otherwise ``~col(<non-boolean key>)`` raises an AnalysisException and the fingerprint
    fail-open silently discards the Stage-2 surgical output."""
    columns = [
        "order_match",  # key column that merely ends in _match
        "status_base",
        "status_compare",
        "status_match",  # the only genuine flag
    ]
    assert _row_mismatch_flag_columns(columns) == ["status_match"]


def test_row_mismatch_flag_columns_handles_compared_column_ending_in_match():
    """A compared column whose own name ends in ``_match`` produces a ``_match_match``
    flag with matching ``_match_base``/``_match_compare`` siblings and must still be found."""
    columns = [
        "id",
        "is_match_base",
        "is_match_compare",
        "is_match_match",
    ]
    assert _row_mismatch_flag_columns(columns) == ["is_match_match"]


def test_row_mismatch_flag_columns_empty_when_no_triples():
    assert _row_mismatch_flag_columns(["id", "order_match", "name"]) == []
