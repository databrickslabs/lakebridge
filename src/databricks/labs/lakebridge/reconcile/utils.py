import logging

from pyspark.sql import SparkSession

from databricks.labs.lakebridge.reconcile.connectors.source_adapter import create_adapter
from databricks.labs.lakebridge.reconcile.exception import InvalidInputException
from databricks.labs.lakebridge.transpiler.sqlglot.dialect_utils import get_dialect

logger = logging.getLogger(__name__)


def initialise_data_source(
    spark: SparkSession,
    source_dialect: str,
    connection_name: str | None,
):
    if not connection_name:
        validate_input(source_dialect, {"databricks"}, "Please configure connection name")
        source = create_adapter(engine=get_dialect("databricks"), spark=spark, connection_name="databricks")
    else:
        source = create_adapter(engine=get_dialect(source_dialect), spark=spark, connection_name=connection_name)

    target = create_adapter(engine=get_dialect("databricks"), spark=spark, connection_name="databricks", is_target=True)

    return source, target


def validate_input(input_value: str, list_of_value: set, message: str):
    if input_value not in list_of_value:
        error_message = f"{message} --> {input_value} is not one of {list_of_value}"
        logger.error(error_message)
        raise InvalidInputException(error_message)
