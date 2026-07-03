"""Timestamp wall-clock fidelity across *all* Lakebridge profiler ingestion paths.

Every profiler writes its extracted source data into a local DuckDB database
through one of two *shared* code paths:

* **SQL profilers** (Oracle, Redshift, MSSQL, Snowflake, Teradata)::

      DatabaseManager.fetch -> FetchResult.to_df() -> PipelineClass._save_to_db

* **Python profilers** (Synapse, BigQuery)::

      pandas.DataFrame -> save_to_duckdb()

These tests verify that both paths preserve timestamp values exactly: a
timezone-*naive* source timestamp stays a naive DuckDB ``TIMESTAMP`` -- its
wall-clock reading unchanged and unaffected by the session time zone -- while a
tz-aware timestamp keeps its instant. They fail loudly if ingestion ever starts
shifting a wall-clock value with the session time zone.
"""

from __future__ import annotations

from collections import namedtuple
from datetime import datetime
from pathlib import Path

import duckdb
import pandas as pd
import pytest

from databricks.labs.lakebridge.assessments.pipeline import PipelineClass, StepExecutionStatus
from databricks.labs.lakebridge.assessments.profiler_config import PipelineConfig, Step
from databricks.labs.lakebridge.connections.database_manager import FetchResult
from databricks.labs.lakebridge.resources.assessments.common.duckdb_helpers import save_to_duckdb

# A fixed wall-clock reading with no time zone attached. This is the value a
# source database reports; fidelity means we read back these exact digits.
NAIVE_WALLCLOCK = pd.Timestamp("2026-04-03 13:55:26")
NAIVE_WALLCLOCK_2 = pd.Timestamp("2026-12-31 23:59:59")

# Session time zones east and west of UTC, plus a whole-day offset. A naive
# DuckDB TIMESTAMP must read back identically under all of them.
SESSION_TIMEZONES = ["America/Los_Angeles", "Asia/Kolkata", "Pacific/Kiritimati"]

# Profilers whose extract flows through DatabaseManager.fetch -> _save_to_db.
SQL_PROFILERS = ["oracle", "redshift", "mssql", "snowflake", "teradata"]


def _read_table(db_path: str, table_name: str, *, session_tz: str | None = None) -> pd.DataFrame:
    with duckdb.connect(db_path) as conn:
        if session_tz is not None:
            conn.execute(f"SET TimeZone='{session_tz}'")
        return conn.execute(f"SELECT * FROM {table_name}").fetchdf()


def _column_types(db_path: str, table_name: str) -> dict[str, str]:
    with duckdb.connect(db_path) as conn:
        rows = conn.execute(f"DESCRIBE {table_name}").fetchall()
    return {row[0]: row[1] for row in rows}


def _is_naive_timestamp(duckdb_type: str) -> bool:
    """True for any timezone-*naive* DuckDB timestamp type.

    DuckDB reports the precision in the type name (``TIMESTAMP``, ``TIMESTAMP_NS``,
    ``TIMESTAMP_US`` ...); pandas ``datetime64[ns]`` lands as ``TIMESTAMP_NS``. What
    matters for fidelity is only that it is *not* ``TIMESTAMP WITH TIME ZONE``.
    """
    return duckdb_type.startswith("TIMESTAMP") and "TIME ZONE" not in duckdb_type


# --------------------------------------------------------------------------- #
# Python-profiler path: save_to_duckdb (Synapse, BigQuery)
# --------------------------------------------------------------------------- #
def test_save_to_duckdb_preserves_naive_wallclock(tmp_path: Path) -> None:
    """A naive source timestamp lands as DuckDB ``TIMESTAMP`` with its wall clock intact."""
    db_path = str(tmp_path / "extract.duckdb")
    df = pd.DataFrame({"id": [1], "event_ts": pd.to_datetime([NAIVE_WALLCLOCK])})
    assert df["event_ts"].dt.tz is None  # precondition: source value is naive

    save_to_duckdb(df, "synapse_like", db_path)

    # Naive in, naive out: a naive TIMESTAMP, *not* TIMESTAMP WITH TIME ZONE.
    assert _is_naive_timestamp(_column_types(db_path, "synapse_like")["event_ts"])
    out = _read_table(db_path, "synapse_like")
    assert out["event_ts"].iloc[0] == NAIVE_WALLCLOCK


def test_save_to_duckdb_preserves_tzaware_instant(tmp_path: Path) -> None:
    """A tz-aware source timestamp (BigQuery pins ``datetime64[ns, UTC]``) keeps its instant."""
    db_path = str(tmp_path / "extract.duckdb")
    aware = pd.Timestamp("2026-04-03 13:55:26", tz="UTC")
    df = pd.DataFrame({"id": [1], "event_ts": pd.to_datetime([aware])})
    assert df["event_ts"].dt.tz is not None  # precondition: source value is an instant

    save_to_duckdb(df, "bigquery_like", db_path)

    assert "TIME ZONE" in _column_types(db_path, "bigquery_like")["event_ts"]
    out = _read_table(db_path, "bigquery_like")
    assert pd.Timestamp(out["event_ts"].iloc[0]).tz_convert("UTC") == aware


@pytest.mark.parametrize("session_tz", SESSION_TIMEZONES)
def test_save_to_duckdb_naive_timestamp_does_not_shift_with_session_tz(tmp_path: Path, session_tz: str) -> None:
    """Reading a naive timestamp under any session time zone yields the same wall clock."""
    db_path = str(tmp_path / "extract.duckdb")
    df = pd.DataFrame({"id": [1], "event_ts": pd.to_datetime([NAIVE_WALLCLOCK])})

    save_to_duckdb(df, "metrics", db_path)

    # The guard that makes this non-tautological: the column must stay a naive
    # TIMESTAMP. If ingestion ever emitted TIMESTAMP WITH TIME ZONE, the value
    # below would move with the session tz.
    assert _is_naive_timestamp(_column_types(db_path, "metrics")["event_ts"])
    out = _read_table(db_path, "metrics", session_tz=session_tz)
    assert out["event_ts"].iloc[0] == NAIVE_WALLCLOCK


# --------------------------------------------------------------------------- #
# SQL-profiler path: DatabaseManager.fetch -> FetchResult.to_df() -> _save_to_db
# (Oracle, Redshift, MSSQL, Snowflake, Teradata)
#
# Driven through the public PipelineClass.execute() with a fake executor, so the
# real ingestion wiring (_execute_sql_step -> _save_to_db) is exercised end to end.
# --------------------------------------------------------------------------- #
# Emulates a driver row: DatabaseManager.fetch returns namedtuple-like rows that
# pandas turns into named columns (see FetchResult.to_df).
_Row = namedtuple("_Row", ["id", "event_ts"])


class _FakeExecutor:
    """Stands in for DatabaseManager: returns a canned FetchResult, ignoring the query.

    Lets the SQL-profiler ingestion path run as a unit test without a live source
    database. Only ``fetch`` is used by ``_execute_sql_step``.
    """

    def __init__(self, result: FetchResult) -> None:
        self._result = result

    def fetch(self, _query: str) -> FetchResult:
        return self._result


def _run_sql_profiler_step(tmp_path: Path, table: str, result: FetchResult) -> str:
    """Run a single ``sql`` step through the public pipeline and return the DuckDB path."""
    sql_file = tmp_path / f"{table}.sql"
    sql_file.write_text("SELECT * FROM source", encoding="utf-8")  # ignored by the fake executor
    config = PipelineConfig(
        name="profiler",
        version="1.0",
        steps=[Step(name=table, type="sql", extract_source=str(sql_file), mode="overwrite")],
    )
    db_path = tmp_path / "extract.duckdb"
    pipeline = PipelineClass(
        config=config,
        executor=_FakeExecutor(result),  # type: ignore[arg-type]
        db_path=db_path,
        cred_file_path=tmp_path / "credentials.yml",
    )
    results = pipeline.execute()
    assert all(r.status is StepExecutionStatus.COMPLETE for r in results)
    return str(db_path)


@pytest.mark.parametrize("profiler", SQL_PROFILERS)
def test_sql_profiler_ingest_preserves_naive_wallclock(tmp_path: Path, profiler: str) -> None:
    """Every SQL profiler's fetch -> _save_to_db path keeps naive timestamps exact."""
    result = FetchResult(
        columns={"id", "event_ts"},
        rows=[
            _Row(1, datetime(2026, 4, 3, 13, 55, 26)),
            _Row(2, datetime(2026, 12, 31, 23, 59, 59)),
        ],
    )
    table = f"{profiler}_events"

    db_path = _run_sql_profiler_step(tmp_path, table, result)

    assert _is_naive_timestamp(_column_types(db_path, table)["event_ts"])
    out = _read_table(db_path, table).sort_values("id").reset_index(drop=True)
    assert out["event_ts"].tolist() == [NAIVE_WALLCLOCK, NAIVE_WALLCLOCK_2]


@pytest.mark.parametrize("profiler", SQL_PROFILERS)
@pytest.mark.parametrize("session_tz", SESSION_TIMEZONES)
def test_sql_profiler_naive_timestamp_does_not_shift_with_session_tz(
    tmp_path: Path, profiler: str, session_tz: str
) -> None:
    """No SQL profiler's naive timestamp shifts when the extract is read under another session tz."""
    result = FetchResult(columns={"id", "event_ts"}, rows=[_Row(1, datetime(2026, 4, 3, 13, 55, 26))])
    table = f"{profiler}_events"

    db_path = _run_sql_profiler_step(tmp_path, table, result)

    # As above: the naive-type assertion is what guards against a regression to
    # TIMESTAMP WITH TIME ZONE; the value check then confirms no session-tz shift.
    assert _is_naive_timestamp(_column_types(db_path, table)["event_ts"])
    out = _read_table(db_path, table, session_tz=session_tz)
    assert out["event_ts"].iloc[0] == NAIVE_WALLCLOCK
