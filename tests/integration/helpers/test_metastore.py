import pytest
from databricks.sdk import WorkspaceClient

from databricks.labs.lakebridge.helpers.metastore import CatalogOperations
from databricks.sdk.service.catalog import SecurableType, Privilege


@pytest.fixture
def catalog_ops(ws: WorkspaceClient):
    return CatalogOperations(ws)


# TODO: Add more tests for Resource Configurator, for now this helps when we upgrade databricks sdk
def test_has_privileges_user_has_all(ws: WorkspaceClient, catalog_ops: CatalogOperations):
    user = ws.current_user.me().user_name
    # Ensure the user has the required privileges in the test environment
    assert catalog_ops.has_privileges(
        user=user,
        securable_type=SecurableType.CATALOG,
        full_name="sandbox",
        privileges={Privilege.ALL_PRIVILEGES},
    )
