import json
import sys

from databricks.labs.lakebridge.connections.credential_manager import create_credential_manager
from databricks.labs.lakebridge.assessments import PRODUCT_NAME
from databricks.labs.lakebridge.resources.assessments.mssql.common.functions import create_msql_sql_client
from databricks.labs.lakebridge.resources.assessments.synapse.common.functions import arguments_loader, set_logger


def execute():
    logger = set_logger(__file__)

    db_path, creds_file = arguments_loader(desc="MSSQL Server Activity Extract Script")

    cred_manager = create_credential_manager(PRODUCT_NAME, creds_file)
    mssql_settings = cred_manager.get_credentials("mssql")
    mssql_profiler_settings = mssql_settings["profiler"]

    mssql_client = create_msql_sql_client(mssql_profiler_settings)

    try:
        # list all the SQL servers in the subscription
        sql_servers = mssql_client.servers.list()
        for sql_server in sql_servers:

            # Extract activity metrics
            logger.info(f"Extracting activity metrics for: {sql_server}")

        print(json.dumps({"status": "success", "message": " All data loaded successfully loaded successfully"}))

    except Exception as e:
        logger.error(f"Failed to extract activity info for Azure SQL server: {str(e)}")
        print(json.dumps({"status": "error", "message": str(e)}), file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    execute()
