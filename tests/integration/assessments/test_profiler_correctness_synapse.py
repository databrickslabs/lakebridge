"""Correctness integration test for the Synapse profiler metadata extracts.

The Synapse profiler's full run drives Azure Synapse REST APIs (workspace info,
monitoring metrics, SQL-pool enumeration) that the test sandbox cannot provide,
so it cannot be executed end to end here. What *can* be exercised against a live
endpoint is the metadata-extraction subset: the ``INFORMATION_SCHEMA``-based
queries (tables/columns/views/routines) run on the SQL Server sandbox, which
stands in for a Synapse SQL pool since they share the T-SQL protocol.

For each of those extracts this test runs the *real* profiler query against the
live sandbox, ingests the result exactly as the profiler does
(``save_to_duckdb(..., schema=SYNAPSE_SCHEMAS[table])``), and asserts the
resulting DuckDB table matches the pinned schema. Because the ingest INSERT is
positional, this also verifies the query's projected columns line up with the
declared schema -- a real bug if they ever drift apart.

Not covered here (require Azure APIs / Synapse-only DMVs, no sandbox): workspace
info, monitoring metrics, SQL-pool enumeration, and the activity extracts backed
by ``SYS.DM_PDW_*`` views. The declared schemas themselves are validated
independently in ``test_synapse_schema_contract.py``.

Requires SQL Server sandbox credentials (``TEST_TSQL_*`` in
``~/.databricks/debug-env.json`` or the environment); skipped when absent, so it
only runs in CI where the secrets are configured.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from databricks.labs.lakebridge.connections.database_manager import DatabaseManager
from databricks.labs.lakebridge.resources.assessments.common.duckdb_helpers import save_to_duckdb
from databricks.labs.lakebridge.resources.assessments.synapse.common.queries import SynapseQueries
from databricks.labs.lakebridge.resources.assessments.synapse.common.schemas import SYNAPSE_SCHEMAS

from tests.integration.assessments.profiler_extract_helpers import (
    actual_schema,
    env_available,
    parse_declared_schema,
)

_TSQL_ENV_KEYS = ("TEST_TSQL_JDBC", "TEST_TSQL_USER", "TEST_TSQL_PASS")

# A placeholder SQL-pool name; the sandbox is a single SQL Server database, so the
# value only ends up as the literal POOL_NAME column in each extract.
_POOL_NAME = "sandbox_pool"

# The metadata extracts whose queries hit INFORMATION_SCHEMA and therefore run on
# the SQL Server stand-in. Each maps its DuckDB table to the query that feeds it.
_METADATA_EXTRACTS: dict[str, Callable[[str], str]] = {
    "dedicated_tables": SynapseQueries.list_tables,
    "dedicated_columns": SynapseQueries.list_columns,
    "dedicated_views": SynapseQueries.list_views,
    "dedicated_routines": SynapseQueries.list_routines,
}

pytestmark = pytest.mark.skipif(
    not env_available(_TSQL_ENV_KEYS),
    reason="SQL Server sandbox credentials not configured (TEST_TSQL_* in debug-env)",
)


@pytest.mark.parametrize("table_name", sorted(_METADATA_EXTRACTS))
def test_synapse_metadata_extract_matches_declared_schema(
    sandbox_synapse: DatabaseManager,
    tmp_path: Path,
    table_name: str,
) -> None:
    query = _METADATA_EXTRACTS[table_name](_POOL_NAME)
    db_path = str(tmp_path / "synapse_extract.duckdb")

    # Run the real profiler query against the live endpoint and ingest it exactly
    # as the Synapse extract does.
    result = sandbox_synapse.fetch(query)
    save_to_duckdb(result.to_df(), table_name, db_path, schema=SYNAPSE_SCHEMAS[table_name])

    assert actual_schema(db_path, table_name) == parse_declared_schema(SYNAPSE_SCHEMAS[table_name])
