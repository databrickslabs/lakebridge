"""Run the real SQL Server profiler against the sandbox database and check every step's output.

The sandbox (``sandbox_sqlserver_config``) is an Azure SQL Database, which always profiles as ``single_db``.
The test creates its own objects in a uniquely named schema -- with non-ASCII names, so name handling is
exercised too -- runs the profiler pipeline end to end, and asserts on the content of each extracted table
against those known objects. The objects are dropped afterwards; the sandbox database is shared.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

import duckdb
import pytest
import sqlglot
import yaml
from databricks.labs.blueprint.installation import JsonObject
from sqlglot import exp

from databricks.labs.lakebridge.assessments import PROFILER_RUN_METADATA_TABLE
from databricks.labs.lakebridge.assessments.pipeline import StepExecutionStatus, make_profiler_db_filename
from databricks.labs.lakebridge.assessments.profiler import Profiler
from databricks.labs.lakebridge.assessments.profiler_config import PipelineConfig
from databricks.labs.lakebridge.assessments.run_metadata import ProfilerRunStatus
from databricks.labs.lakebridge.connections.database_manager import create_connector

_REPO_ROOT = Path(__file__).resolve().parents[3]
_MSSQL = _REPO_ROOT / "src/databricks/labs/lakebridge/resources/assessments/mssql"
_COMMAND_TYPES = {"QUERY", "DML", "DDL", "ROUTINE", "TRANSACTION_CONTROL", "OTHER"}


@dataclass(frozen=True)
class SandboxObjects:
    """The objects the test creates, and facts about the database it runs in."""

    database: str
    collation: str
    db_id: int
    schema: str
    table: str
    view: str
    indexed_view: str
    index: str
    procedure: str
    procedure_runs: int = 2
    table_rows: int = 3


@pytest.fixture()
def sandbox_objects(
    sandbox_sqlserver_config: JsonObject, make_random: Callable[[int], str]
) -> Iterator[SandboxObjects]:
    suffix = make_random(6).lower()
    objects_ = SandboxObjects(
        database="",
        collation="",
        db_id=0,
        schema=f"lb_ñ_{suffix}",
        table=f"Pedidos_表_{suffix}",
        view="Vista_é",
        indexed_view="IVista_ö",
        index="IX_ö",
        procedure="Proc_ü",
    )
    schema_ref, table_ref = f"[{objects_.schema}]", f"[{objects_.schema}].[{objects_.table}]"
    with create_connector("mssql", sandbox_sqlserver_config) as conn:
        database, collation, db_id = conn.fetch(
            "SELECT DB_NAME(), CAST(DATABASEPROPERTYEX(DB_NAME(), 'Collation') AS nvarchar(128)), DB_ID()"
        ).rows[0]
        objects_ = SandboxObjects(**{**objects_.__dict__, "database": database, "collation": collation, "db_id": db_id})
        for statement in (
            f"CREATE SCHEMA {schema_ref}",
            f"CREATE TABLE {table_ref} (id INT PRIMARY KEY, [Größe] NVARCHAR(20) NULL, amount DECIMAL(10, 2) NOT NULL)",
            f"INSERT INTO {table_ref} VALUES (1, N'klein', 1.50), (2, N'mittel', 2.50), (3, NULL, 3.75)",
            f"CREATE VIEW {schema_ref}.[{objects_.view}] AS SELECT id, [Größe] FROM {table_ref}",
            f"CREATE VIEW {schema_ref}.[{objects_.indexed_view}] WITH SCHEMABINDING AS SELECT id, amount FROM {table_ref}",
            f"CREATE UNIQUE CLUSTERED INDEX [{objects_.index}] ON {schema_ref}.[{objects_.indexed_view}] (id)",
            f"CREATE PROCEDURE {schema_ref}.[{objects_.procedure}] AS SELECT COUNT(*) FROM {table_ref}",
        ):
            conn.fetch(statement)
        for _ in range(objects_.procedure_runs):
            conn.fetch(f"EXEC {schema_ref}.[{objects_.procedure}]")
    try:
        yield objects_
    finally:
        with create_connector("mssql", sandbox_sqlserver_config) as conn:
            for statement in (
                f"DROP PROCEDURE IF EXISTS {schema_ref}.[{objects_.procedure}]",
                f"DROP VIEW IF EXISTS {schema_ref}.[{objects_.indexed_view}]",
                f"DROP VIEW IF EXISTS {schema_ref}.[{objects_.view}]",
                f"DROP TABLE IF EXISTS {table_ref}",
                f"DROP SCHEMA IF EXISTS {schema_ref}",
            ):
                conn.fetch(statement)


def _pipeline_config() -> PipelineConfig:
    # Configs and SQL come from this checkout, not an installed copy of lakebridge.
    return Profiler.path_modifier(config_file=_MSSQL / "single_db/pipeline_config.yml", path_prefix=_REPO_ROOT)


def _sql_steps(config: PipelineConfig) -> list[str]:
    return [step.name for step in config.steps if step.type == "sql" and step.flag == "active"]


def _ddl_column_names(step: str) -> list[str]:
    create = sqlglot.parse_one((_MSSQL / f"{step}_ddl.sql").read_text(encoding="utf-8"), read="duckdb")
    assert isinstance(create, exp.Create) and isinstance(create.this, exp.Schema)
    return [col.name.upper() for col in create.this.expressions if isinstance(col, exp.ColumnDef)]


@pytest.fixture()
def extract(sandbox_sqlserver_config: JsonObject, sandbox_objects: SandboxObjects, tmp_path: Path) -> Path:
    """Profile the sandbox with the real pipeline; return the DuckDB extract."""
    creds = tmp_path / ".credentials.yml"
    creds.write_text(yaml.safe_dump({"secret_vault_type": "local", "mssql": dict(sandbox_sqlserver_config)}))
    creds.chmod(0o600)
    output = tmp_path / "output"
    Profiler("mssql", "single_db", _pipeline_config()).profile(output_folder=output, cred_file_path=creds)
    return output / make_profiler_db_filename("mssql")


def _rows(conn: duckdb.DuckDBPyConnection, table: str, where: str = "", params: list | None = None) -> list[dict]:
    cursor = conn.execute(f"SELECT * FROM {table} {where}", params or [])
    names = [d[0].upper() for d in cursor.description]
    return [dict(zip(names, row)) for row in cursor.fetchall()]


def test_query_results_match_the_ddl(sandbox_sqlserver_config: JsonObject) -> None:
    # The pipeline writes by position, so a renamed or reordered column would load silently into the
    # wrong DuckDB column; compare the live result set of every query against its DDL instead.
    mismatches = {}
    with create_connector("mssql", sandbox_sqlserver_config) as conn:
        for step in _pipeline_config().steps:
            if step.type != "sql" or step.flag != "active":
                continue
            columns = [c.upper() for c in conn.fetch(Path(step.extract_source).read_text(encoding="utf-8")).columns]
            if columns != _ddl_column_names(step.name):
                mismatches[step.name] = (columns, _ddl_column_names(step.name))
    assert not mismatches


def test_every_step_completes(extract: Path) -> None:
    with duckdb.connect(str(extract), read_only=True) as conn:
        metadata = conn.execute(f"SELECT status, results FROM {PROFILER_RUN_METADATA_TABLE}").fetchone()
    assert metadata is not None, "the run wrote no metadata row"
    status, results = metadata
    by_step = {r["step_name"]: r for r in json.loads(results)}
    assert status == ProfilerRunStatus.COMPLETE.value, by_step
    assert set(by_step) == set(_sql_steps(_pipeline_config()))
    assert {name for name, r in by_step.items() if r["status"] != StepExecutionStatus.COMPLETE.value} == set()


def test_sys_info(extract: Path) -> None:
    with duckdb.connect(str(extract), read_only=True) as conn:
        rows = _rows(conn, "sys_info")
    assert len(rows) == 1
    row = rows[0]
    assert row["CPU_COUNT"] >= 1
    assert row["SCHEDULER_COUNT"] >= 1
    assert row["PHYSICAL_MEMORY_KB"] > 0
    assert row["SQLSERVER_START_TIME"] is not None
    assert row["EXTRACT_TS"] is not None
    assert row["SQLSERVER_START_TIME"] <= row["EXTRACT_TS"]


def test_databases(extract: Path, sandbox_objects: SandboxObjects) -> None:
    with duckdb.connect(str(extract), read_only=True) as conn:
        rows = _rows(conn, "databases")
    names = {r["NAME"] for r in rows}
    assert not names & {"master", "tempdb", "model", "msdb"}
    ours = [r for r in rows if r["NAME"] == sandbox_objects.database]
    assert len(ours) == 1
    assert ours[0]["DB_ID"] == sandbox_objects.db_id
    assert ours[0]["COLLATION_NAME"] == sandbox_objects.collation
    assert ours[0]["CREATE_DATE"] is not None


def test_tables(extract: Path, sandbox_objects: SandboxObjects) -> None:
    objs = sandbox_objects
    with duckdb.connect(str(extract), read_only=True) as conn:
        rows = _rows(conn, "tables", "WHERE TABLE_SCHEMA = ?", [objs.schema])
    assert {(r["TABLE_NAME"], r["TABLE_TYPE"]) for r in rows} == {
        (objs.table, "BASE TABLE"),
        (objs.view, "VIEW"),
        (objs.indexed_view, "VIEW"),
    }
    assert {(r["DATABASE_NAME"], r["TABLE_CATALOG"]) for r in rows} == {(objs.database, objs.database)}


def test_views_are_listed_with_redacted_definitions(extract: Path, sandbox_objects: SandboxObjects) -> None:
    objs = sandbox_objects
    with duckdb.connect(str(extract), read_only=True) as conn:
        ours = _rows(conn, "views", "WHERE TABLE_SCHEMA = ?", [objs.schema])
        definitions = {r[0] for r in conn.execute("SELECT DISTINCT VIEW_DEFINITION FROM views").fetchall()}
    assert {r["TABLE_NAME"] for r in ours} == {objs.view, objs.indexed_view}
    assert {r["DATABASE_NAME"] for r in ours} == {objs.database}
    # View source must never leave the customer's database, for any view.
    assert definitions == {"[REDACTED]"}


def test_columns(extract: Path, sandbox_objects: SandboxObjects) -> None:
    objs = sandbox_objects
    with duckdb.connect(str(extract), read_only=True) as conn:
        rows = _rows(
            conn,
            "columns",
            "WHERE TABLE_SCHEMA = ? AND TABLE_NAME = ? ORDER BY ORDINAL_POSITION",
            [objs.schema, objs.table],
        )
    assert [r["COLUMN_NAME"] for r in rows] == ["id", "Größe", "amount"]
    assert [r["ORDINAL_POSITION"] for r in rows] == [1, 2, 3]
    assert [r["DATA_TYPE"] for r in rows] == ["int", "nvarchar", "decimal"]
    assert [r["IS_NULLABLE"] for r in rows] == ["NO", "YES", "NO"]
    assert rows[1]["CHARACTER_MAXIMUM_LENGTH"] == 20
    assert (rows[2]["NUMERIC_PRECISION"], rows[2]["NUMERIC_SCALE"]) == (10, 2)
    assert {r["DATABASE_NAME"] for r in rows} == {objs.database}


def test_indexed_views(extract: Path, sandbox_objects: SandboxObjects) -> None:
    objs = sandbox_objects
    with duckdb.connect(str(extract), read_only=True) as conn:
        rows = _rows(conn, "indexed_views", "WHERE SCHEMA_NAME = ?", [objs.schema])
    assert [(r["INDEXED_VIEW_NAME"], r["INDEX_NAME"], r["INDEX_TYPE"], r["INDEX_ID"]) for r in rows] == [
        (objs.indexed_view, objs.index, "CLUSTERED", 1)
    ]
    assert rows[0]["DATABASE_NAME"] == objs.database


def test_routines_are_listed_with_redacted_definitions(extract: Path, sandbox_objects: SandboxObjects) -> None:
    objs = sandbox_objects
    with duckdb.connect(str(extract), read_only=True) as conn:
        ours = _rows(conn, "routines", "WHERE ROUTINE_SCHEMA = ?", [objs.schema])
        definitions = {r[0] for r in conn.execute("SELECT DISTINCT ROUTINE_DEFINITION FROM routines").fetchall()}
    assert [(r["ROUTINE_NAME"], r["ROUTINE_TYPE"]) for r in ours] == [(objs.procedure, "PROCEDURE")]
    assert ours[0]["DATABASE_NAME"] == objs.database
    assert definitions == {"[REDACTED]"}


def test_db_sizes(extract: Path, sandbox_objects: SandboxObjects) -> None:
    with duckdb.connect(str(extract), read_only=True) as conn:
        rows = _rows(conn, "db_sizes")
    assert rows
    assert {r["DATABASE_NAME"] for r in rows} == {sandbox_objects.database}
    for data_file in rows:
        assert data_file["FILENAME"]
        assert data_file["CURRENTSIZEMB"] > 0
        assert 0 <= data_file["FREESPACEINMB"] <= data_file["CURRENTSIZEMB"]


def test_table_sizes(extract: Path, sandbox_objects: SandboxObjects) -> None:
    objs = sandbox_objects
    with duckdb.connect(str(extract), read_only=True) as conn:
        rows = _rows(conn, "table_sizes", "WHERE TABLENAME = ?", [objs.table])
    assert len(rows) == 1
    row = rows[0]
    assert row["DATABASE_NAME"] == objs.database
    assert row["ROWCOUNT"] == objs.table_rows
    assert row["RESERVEDMB"] >= row["USEDMB"] >= 0


def test_proc_stats(extract: Path, sandbox_objects: SandboxObjects) -> None:
    objs = sandbox_objects
    with duckdb.connect(str(extract), read_only=True) as conn:
        rows = _rows(conn, "proc_stats", "WHERE OBJECT_NAME = ?", [objs.procedure])
    assert len(rows) == 1
    row = rows[0]
    assert row["DB_NAME"] == objs.database
    assert row["TYPE"].strip() == "P"
    assert row["EXECUTION_COUNT"] >= objs.procedure_runs
    assert row["LAST_EXECUTION_TIME"] is not None


def test_query_stats(extract: Path) -> None:
    with duckdb.connect(str(extract), read_only=True) as conn:
        rows = _rows(conn, "query_stats")
    assert rows
    assert {r["COMMAND_TYPE"] for r in rows} <= _COMMAND_TYPES
    assert all(r["EXECUTION_COUNT"] >= 1 for r in rows)


def test_sessions_hash_login_names(extract: Path, sandbox_sqlserver_config: JsonObject) -> None:
    with duckdb.connect(str(extract), read_only=True) as conn:
        rows = _rows(conn, "sessions")
    assert rows  # at least the profiler's own session
    # Login names are SHA2_256-hashed (hex, truncated to 64 characters); the plain login must never appear.
    assert all(re.fullmatch(r"0x[0-9A-F]{62}", r["LOGIN_NAME"]) for r in rows)
    assert sandbox_sqlserver_config["user"] not in {r["LOGIN_NAME"] for r in rows}


def test_cpu_utilization(extract: Path) -> None:
    with duckdb.connect(str(extract), read_only=True) as conn:
        rows = _rows(conn, "cpu_utilization")
    # The scheduler ring buffer can legitimately be empty on a fresh server; any row must be a valid sample.
    for sample in rows:
        assert sample["EVENTTIME"] is not None
        assert 0 <= sample["SYSTEMIDLE"] <= 100
        assert 0 <= sample["SQLPROCESSUTILIZATION"] <= 100
