from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class ProfilerRunMetadata:
    source_system: str
    variant: str | None
    lakebridge_version: str
    python_version: str
    operating_system: str
    generated_at: datetime


# Declared rather than inferred: `variant` is NULL for sources without variants, and DuckDB
# types an all-null column INTEGER, so inference would give the same column a different type
# from one extract to the next. Column order must match the field order above: the row is
# inserted positionally.
PROFILER_RUN_METADATA_SCHEMA = (
    "source_system VARCHAR, "
    "variant VARCHAR, "
    "lakebridge_version VARCHAR, "
    "python_version VARCHAR, "
    "operating_system VARCHAR, "
    "generated_at TIMESTAMPTZ"
)
