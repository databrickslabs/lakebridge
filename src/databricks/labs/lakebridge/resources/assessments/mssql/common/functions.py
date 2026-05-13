from azure.identity import DefaultAzureCredential
from azure.mgmt.sql import SqlManagementClient


def create_msql_sql_client(config: dict) -> SqlManagementClient:
    """
    Creates an Azure SQL management client for the provided subscription using the default Azure credential.
    """
    return SqlManagementClient(credential=DefaultAzureCredential(), subscription_id=config["subscription_id"])
