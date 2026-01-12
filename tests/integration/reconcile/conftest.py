import logging

import pytest

from databricks.sdk import WorkspaceClient
from databricks.sdk.errors.platform import PermissionDenied
from databricks.sdk.service.catalog import TableInfo, SchemaInfo
from tests.integration.debug_envgetter import TestEnvGetter

logger = logging.getLogger(__name__)

DIAMONDS_ROWS_SQL = """
                    INSERT INTO {catalog}.{schema}.{table} (carat, cut, color, clarity) VALUES
                        (0.23, 'Ideal', 'E', 'SI2'),
                        (0.21, 'Premium', 'E', 'SI1'),
                        (0.23, 'Good', 'E', 'VS1'),
                        (0.29, 'Premium', 'I', 'VS2'),
                        (0.29, 'Gold', 'Invariant', 'VS22'),
                        (0.31, 'Good', 'J', 'SI2'); \
                    """


@pytest.fixture
def recon_catalog(make_catalog) -> str:
    try:
        catalog = make_catalog().name
    except PermissionDenied as e:
        logger.warning("Could not create catalog for recon tests, using 'sandbox' instead", exc_info=e)
        catalog = "sandbox"

    return catalog


@pytest.fixture
def recon_schema(recon_catalog, make_schema) -> SchemaInfo:
    from_schema = make_schema(catalog_name=recon_catalog)

    return from_schema


@pytest.fixture
def recon_tables(ws: WorkspaceClient, recon_schema: SchemaInfo, make_table) -> tuple[TableInfo, TableInfo]:
    src_table = make_table(catalog_name=recon_schema.catalog_name, schema_name=recon_schema.name)
    tgt_table = make_table(catalog_name=recon_schema.catalog_name, schema_name=recon_schema.name)

    test_env = TestEnvGetter(True)
    warehouse = test_env.get("TEST_DEFAULT_WAREHOUSE_ID")

    for tbl in (src_table, tgt_table):
        sql = DIAMONDS_ROWS_SQL.format(
            catalog=recon_schema.catalog_name,
            schema=recon_schema.name,
            table=tbl.name,
        )
        ws.statement_execution.execute_statement(
            warehouse_id=warehouse,
            catalog=recon_schema.catalog_name,
            schema=recon_schema.name,
            statement=sql,
        )

    return src_table, tgt_table
