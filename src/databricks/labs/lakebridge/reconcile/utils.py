import logging

from pyspark.sql import SparkSession

from databricks.sdk import WorkspaceClient

from databricks.labs.lakebridge.config import ReconcileMetadataConfig
from databricks.labs.lakebridge.reconcile.connectors.source_adapter import create_adapter
from databricks.labs.lakebridge.reconcile.exception import InvalidInputException
from databricks.labs.lakebridge.reconcile.recon_config import Table
from databricks.labs.lakebridge.transpiler.sqlglot.dialect_utils import get_dialect

logger = logging.getLogger(__name__)


def initialise_data_source(
    ws: WorkspaceClient,
    spark: SparkSession,
    engine: str,
    secret_scope: str,
):
    source = create_adapter(engine=get_dialect(engine), spark=spark, ws=ws, secret_scope=secret_scope)
    target = create_adapter(engine=get_dialect("databricks"), spark=spark, ws=ws, secret_scope=secret_scope)

    return source, target


def validate_input(input_value: str, list_of_value: set, message: str):
    if input_value not in list_of_value:
        error_message = f"{message} --> {input_value} is not one of {list_of_value}"
        logger.error(error_message)
        raise InvalidInputException(error_message)


def generate_volume_path(table_conf: Table, metadata_config: ReconcileMetadataConfig):
    catalog = metadata_config.catalog
    schema = metadata_config.schema
    return f"/Volumes/{catalog}/{schema}/{metadata_config.volume}/{table_conf.source_name}_{table_conf.target_name}/"
