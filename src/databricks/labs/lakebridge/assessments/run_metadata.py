from __future__ import annotations

from dataclasses import dataclass, field, fields
from datetime import datetime
from enum import Enum


class ProfilerRunStatus(str, Enum):
    COMPLETE = "COMPLETE"
    COMPLETE_WITH_ABSENCES = "COMPLETE_WITH_ABSENCES"
    FAILED = "FAILED"


@dataclass(frozen=True)
class ProfilerRunMetadata:
    source_system: str = field(metadata={"duckdb_type": "VARCHAR"})
    variant: str | None = field(metadata={"duckdb_type": "VARCHAR"})
    pipeline_name: str = field(metadata={"duckdb_type": "VARCHAR"})
    pipeline_version: str = field(metadata={"duckdb_type": "VARCHAR"})
    lakebridge_version: str = field(metadata={"duckdb_type": "VARCHAR"})
    python_version: str = field(metadata={"duckdb_type": "VARCHAR"})
    operating_system: str = field(metadata={"duckdb_type": "VARCHAR"})
    status: str = field(metadata={"duckdb_type": "VARCHAR"})
    results: str = field(metadata={"duckdb_type": "VARCHAR"})
    generated_at: datetime = field(metadata={"duckdb_type": "TIMESTAMPTZ"})


# Declared rather than inferred: `variant` is NULL for sources without variants, and DuckDB
# types an all-null column INTEGER, so inference would give the same column a different type
# from one extract to the next. Derived from the dataclass so column order can only come
# from one place — the row is inserted positionally (`INSERT ... SELECT *`).
PROFILER_RUN_METADATA_SCHEMA = ", ".join(f"{f.name} {f.metadata['duckdb_type']}" for f in fields(ProfilerRunMetadata))
