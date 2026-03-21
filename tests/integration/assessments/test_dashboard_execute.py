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

    def createDataFrame(self, pdf: Any) -> _SparkDfStub:  # noqa: N802
        self.last_pdf = pdf
        return _SparkDfStub(self.writer)


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
