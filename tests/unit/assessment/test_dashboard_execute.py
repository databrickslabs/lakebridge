from __future__ import annotations

from pathlib import Path
from typing import Any

import duckdb

from databricks.labs.lakebridge.assessments.dashboards import execute as dashboard_execute


class _WriterStub:
    def __init__(self) -> None:
        self.saved_table: str | None = None

    def format(self, _fmt: str) -> "_WriterStub":
        return self

    def mode(self, _mode: str) -> "_WriterStub":
        return self

    def saveAsTable(self, table_name: str) -> None:
        self.saved_table = table_name


class _SparkDfStub:
    def __init__(self, writer: _WriterStub) -> None:
        self.write = writer


class _SparkSessionStub:
    def __init__(self) -> None:
        self.last_pdf: Any = None
        self.writer = _WriterStub()
        self.sql_commands: list[str] = []

    def createDataFrame(self, pdf: Any) -> _SparkDfStub:  # noqa: N802
        self.last_pdf = pdf
        return _SparkDfStub(self.writer)

    def sql(self, statement: str) -> None:
        self.sql_commands.append(statement)


def test_ingest_table_preserves_multilingual_text(tmp_path: Path, monkeypatch) -> None:
    extract_path = tmp_path / "profiler_extract.db"
    with duckdb.connect(str(extract_path)) as conn:
        conn.execute(
            """
            CREATE TABLE td_multilingual (
                app_id VARCHAR,
                user_name VARCHAR,
                sql_text_info VARCHAR
            )
            """
        )
        conn.execute(
            """
            INSERT INTO td_multilingual VALUES
              ('jp-app', '山田太郎', 'SELECT ''こんにちは'' AS msg'),
              ('kr-app', '홍길동', 'SELECT ''안녕하세요'' AS msg'),
              ('th-app', 'สมชาย', 'SELECT ''สวัสดี'' AS msg'),
              ('fr-app', 'Jean Dupont', 'SELECT ''Bonjour'' AS msg'),
              ('de-app', 'Müller', 'SELECT ''Guten Tag'' AS msg'),
              ('it-app', 'Giovanni Rossi', 'SELECT ''Ciao'' AS msg'),
              ('es-app', 'José García', 'SELECT ''Hola'' AS msg'),
              ('pt-app', 'João Silva', 'SELECT ''Olá'' AS msg'),
              ('nl-app', 'Pieter de Vries', 'SELECT ''Hallo'' AS msg')
            """
        )

    spark_stub = _SparkSessionStub()

    class _BuilderStub:
        @staticmethod
        def getOrCreate() -> _SparkSessionStub:  # noqa: N802
            return spark_stub

    monkeypatch.setattr(dashboard_execute.SparkSession, "builder", _BuilderStub())

    dashboard_execute._ingest_table(
        extract_location=str(extract_path),
        source_table_name="main.td_multilingual",
        target_table_name="lakebridge_profiler.profiler_runs.td_multilingual",
    )

    assert spark_stub.last_pdf is not None
    extracted = list(spark_stub.last_pdf["user_name"])
    assert extracted == [
        "山田太郎",
        "홍길동",
        "สมชาย",
        "Jean Dupont",
        "Müller",
        "Giovanni Rossi",
        "José García",
        "João Silva",
        "Pieter de Vries",
    ]
    assert spark_stub.writer.saved_table == "lakebridge_profiler.profiler_runs.td_multilingual"


def test_apply_sensitive_mask_enabled_adds_mask(monkeypatch) -> None:
    spark_stub = _SparkSessionStub()
    monkeypatch.setenv("LAKEBRIDGE_ENABLE_SQLTEXT_MASK", "true")
    monkeypatch.setenv("LAKEBRIDGE_SQLTEXT_MASK_BYPASS_GROUP", "data-governance-admins")

    dashboard_execute._apply_sensitive_column_mask(
        spark=spark_stub,
        fq_table_name="ad_demo_catalog.bronze.td_dbql_core_info_extract",
        table_name="td_dbql_core_info_extract",
    )

    assert len(spark_stub.sql_commands) == 2
    assert "CREATE FUNCTION IF NOT EXISTS ad_demo_catalog.bronze.mask_sql_textinfo" in spark_stub.sql_commands[0]
    assert "is_account_group_member('data-governance-admins')" in spark_stub.sql_commands[0]
    assert (
        "ALTER TABLE ad_demo_catalog.bronze.td_dbql_core_info_extract "
        "ALTER COLUMN SQLTextInfo SET MASK ad_demo_catalog.bronze.mask_sql_textinfo"
    ) in spark_stub.sql_commands[1]


def test_apply_sensitive_mask_disabled_noop(monkeypatch) -> None:
    spark_stub = _SparkSessionStub()
    monkeypatch.delenv("LAKEBRIDGE_ENABLE_SQLTEXT_MASK", raising=False)

    dashboard_execute._apply_sensitive_column_mask(
        spark=spark_stub,
        fq_table_name="ad_demo_catalog.bronze.td_dbql_core_info_extract",
        table_name="td_dbql_core_info_extract",
    )

    assert spark_stub.sql_commands == []


def test_ingest_table_applies_sqltextinfo_governance_controls(tmp_path: Path, monkeypatch) -> None:
    extract_path = tmp_path / "profiler_extract.db"
    with duckdb.connect(str(extract_path)) as conn:
        conn.execute(
            """
            CREATE TABLE td_dbql_core_info_extract (
                AppID VARCHAR,
                UserName VARCHAR,
                SessionID BIGINT,
                SQLTextInfo VARCHAR,
                StartTime TIMESTAMP,
                FirstRespTime TIMESTAMP,
                TotalFirstRespTime DOUBLE,
                TotalCPUTime DOUBLE,
                TotalIOCount DOUBLE,
                ReqPhysIOKB DOUBLE,
                SpoolUsage DOUBLE
            )
            """
        )
        conn.execute(
            """
            INSERT INTO td_dbql_core_info_extract VALUES
              ('app-a', 'user-a', 1, 'SELECT * FROM sensitive_table WHERE token = ''abc''',
               CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, 1.0, 2.0, 3.0, 4.0, 5.0)
            """
        )

    spark_stub = _SparkSessionStub()

    class _BuilderStub:
        @staticmethod
        def getOrCreate() -> _SparkSessionStub:  # noqa: N802
            return spark_stub

    monkeypatch.setattr(dashboard_execute.SparkSession, "builder", _BuilderStub())
    monkeypatch.setenv("LAKEBRIDGE_ENABLE_SQLTEXT_MASK", "true")
    monkeypatch.setenv("LAKEBRIDGE_SQLTEXT_MASK_BYPASS_GROUP", "data-governance-admins")

    dashboard_execute._ingest_table(
        extract_location=str(extract_path),
        source_table_name="main.td_dbql_core_info_extract",
        target_table_name="ad_demo_catalog.bronze.td_dbql_core_info_extract",
    )

    sql_script = "\n".join(spark_stub.sql_commands)
    assert "ALTER TABLE ad_demo_catalog.bronze.td_dbql_core_info_extract ALTER COLUMN SQLTextInfo COMMENT" in sql_script
    assert "ALTER COLUMN SQLTextInfo SET TAGS ('sensitivity' = 'confidential')" in sql_script
    assert "CREATE FUNCTION IF NOT EXISTS ad_demo_catalog.bronze.mask_sql_textinfo" in sql_script
    assert "is_account_group_member('data-governance-admins')" in sql_script
    assert "ALTER COLUMN SQLTextInfo SET MASK ad_demo_catalog.bronze.mask_sql_textinfo" in sql_script
