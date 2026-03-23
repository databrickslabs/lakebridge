"""Reusable Spark writer/dataframe/session stubs for unit tests."""

from __future__ import annotations

from typing import Any


class WriterStub:
    def __init__(self) -> None:
        self.saved_table: str | None = None

    def format(self, _fmt: str) -> "WriterStub":
        return self

    def mode(self, _mode: str) -> "WriterStub":
        return self

    def saveAsTable(self, table_name: str) -> None:  # noqa: N802
        self.saved_table = table_name


class SparkDfStub:
    def __init__(self, writer: WriterStub) -> None:
        self.write = writer


class SparkSessionStub:
    def __init__(self) -> None:
        self.last_pdf: Any = None
        self.last_schema: Any = None
        self.writer = WriterStub()
        self.sql_commands: list[str] = []
        self.ingested: list[tuple[Any, WriterStub]] = []

    def createDataFrame(self, pdf: Any, schema: Any = None) -> SparkDfStub:  # noqa: N802
        writer = WriterStub()
        self.last_pdf = pdf
        self.last_schema = schema
        self.writer = writer
        self.ingested.append((pdf, writer))
        return SparkDfStub(writer)

    def sql(self, statement: str) -> None:
        self.sql_commands.append(statement)
