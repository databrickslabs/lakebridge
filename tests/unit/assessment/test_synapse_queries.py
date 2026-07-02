"""Unit tests for the Synapse activity query builders and their pinned schemas.

These guard the invariant that a ``WORKSPACE_NAME`` column is emitted immediately
before ``POOL_NAME`` in both the generated SQL and the pinned DuckDB schema. The
DuckDB writer inserts positionally (``INSERT ... SELECT *``), so the SQL column
order and the schema-string column order must stay aligned. ``WORKSPACE_NAME``
lets downstream consumers disambiguate pools that share a name across workspaces.
"""

import pytest

from databricks.labs.lakebridge.resources.assessments.synapse.common.queries import SynapseQueries
from databricks.labs.lakebridge.resources.assessments.synapse.common.schemas import SYNAPSE_SCHEMAS

WORKSPACE = "ws-analytics-eastus"
POOL = "sqlpool01"

# (generated SQL, matching pinned-schema table name)
ACTIVITY_QUERIES = [
    (SynapseQueries.list_dedicated_sessions(POOL, WORKSPACE), "dedicated_sessions"),
    (SynapseQueries.list_dedicated_requests(POOL, WORKSPACE), "dedicated_session_requests"),
    (SynapseQueries.list_serverless_sessions(POOL, WORKSPACE), "serverless_sessions"),
    (SynapseQueries.list_serverless_requests(POOL, WORKSPACE, None), "serverless_session_requests"),
    (SynapseQueries.get_db_storage_info(POOL, WORKSPACE), "dedicated_storage_info"),
    (SynapseQueries.data_processed(POOL, WORKSPACE), "serverless_data_processed"),
]

ACTIVITY_TABLES = [table_name for _, table_name in ACTIVITY_QUERIES]


@pytest.mark.parametrize("sql, table_name", ACTIVITY_QUERIES)
def test_activity_query_emits_workspace_name_before_pool_name(sql, table_name):
    upper = sql.upper()
    assert "WORKSPACE_NAME" in upper, f"{table_name} query is missing WORKSPACE_NAME"
    assert WORKSPACE in sql, f"{table_name} query did not interpolate the workspace name value"
    assert upper.index("WORKSPACE_NAME") < upper.index("POOL_NAME"), (
        f"{table_name}: WORKSPACE_NAME must be selected before POOL_NAME to stay aligned "
        "with the pinned schema (positional INSERT)"
    )


@pytest.mark.parametrize("table_name", ACTIVITY_TABLES)
def test_activity_schema_matches_query_column_order(table_name):
    schema = SYNAPSE_SCHEMAS[table_name].upper()
    assert "WORKSPACE_NAME STRING" in schema, f"{table_name} schema is missing WORKSPACE_NAME"
    assert schema.index("WORKSPACE_NAME") < schema.index(
        "POOL_NAME"
    ), f"{table_name}: schema must declare WORKSPACE_NAME before POOL_NAME"
