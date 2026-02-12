import logging
import tempfile
import uuid
from collections.abc import Generator
from pathlib import Path

import pytest

from pyspark.sql import DataFrame

from databricks.sdk import WorkspaceClient
from databricks.sdk.errors.platform import PermissionDenied
from databricks.sdk.service.catalog import TableInfo, SchemaInfo

from databricks.labs.lakebridge.config import ReconcileMetadataConfig
from databricks.labs.lakebridge.reconcile.recon_capture import AbstractReconIntermediatePersist
from tests.integration.debug_envgetter import TestEnvGetter

logger = logging.getLogger(__name__)

DIAMONDS_COLUMNS = [
    ("carat", "DOUBLE"),
    ("cut", "STRING"),
    ("color", "STRING"),
    ("clarity", "STRING"),
]
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
        logger.info(f"Created catalog {catalog} for recon tests")
    except PermissionDenied as e:
        logger.warning("Could not create catalog for recon tests, using 'sandbox' instead", exc_info=e)
        catalog = "sandbox"

    return catalog


@pytest.fixture
def recon_schema(recon_catalog, make_schema) -> SchemaInfo:
    from_schema = make_schema(catalog_name=recon_catalog)
    logger.info(f"Created schema {from_schema.name} in catalog {recon_catalog} for recon tests")

    return from_schema


@pytest.fixture
def recon_tables(ws: WorkspaceClient, recon_schema: SchemaInfo, make_table) -> tuple[TableInfo, TableInfo]:
    src_table = make_table(
        catalog_name=recon_schema.catalog_name, schema_name=recon_schema.name, columns=DIAMONDS_COLUMNS
    )
    tgt_table = make_table(
        catalog_name=recon_schema.catalog_name, schema_name=recon_schema.name, columns=DIAMONDS_COLUMNS
    )
    logger.info(f"Created recon tables {src_table.name}, {tgt_table.name} in schema {recon_schema.name}")

    test_env = TestEnvGetter(True)
    warehouse = test_env.get("TEST_DEFAULT_WAREHOUSE_ID")

    for tbl in (src_table, tgt_table):
        sql = DIAMONDS_ROWS_SQL.format(
            catalog=recon_schema.catalog_name,
            schema=recon_schema.name,
            table=tbl.name,
        )
        exc_response = ws.statement_execution.execute_statement(
            warehouse_id=warehouse,
            catalog=recon_schema.catalog_name,
            schema=recon_schema.name,
            statement=sql,
        )
        logger.info(f"Inserted data into table {tbl.name} and got response {exc_response.status}")

    return src_table, tgt_table


@pytest.fixture
def recon_metadata(mock_spark, report_tables_schema) -> Generator[ReconcileMetadataConfig, None, None]:
    rand = uuid.uuid4().hex
    schema = f"recon_schema_{rand}"
    mock_spark.sql(f"CREATE SCHEMA {schema}")
    main_schema, metrics_schema, details_schema = report_tables_schema

    mock_spark.createDataFrame(data=[], schema=main_schema).write.saveAsTable(f"{schema}.MAIN")
    mock_spark.createDataFrame(data=[], schema=metrics_schema).write.saveAsTable(f"{schema}.METRICS")
    mock_spark.createDataFrame(data=[], schema=details_schema).write.saveAsTable(f"{schema}.DETAILS")

    yield ReconcileMetadataConfig(
        catalog=f"recon_catalog_{rand}",
        schema=schema,
        volume=f"recon_volume_{rand}",
    )

    mock_spark.sql(f"DROP SCHEMA {schema} CASCADE")


class FakeReconIntermediatePersist(AbstractReconIntermediatePersist):
    @property
    def base_dir(self) -> Path:
        return Path(tempfile.gettempdir())

    @property
    def is_serverless(self) -> bool:
        return False

    def write_and_read_df_with_volumes(
        self,
        df: DataFrame,
    ) -> DataFrame:
        return df
