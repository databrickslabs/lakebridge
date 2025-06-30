import argparse
import json
import sys
import pandas as pd
import duckdb
import logging
import yaml
from azure.identity import DefaultAzureCredential
from azure.monitor.query import MetricsQueryClient
from azure.synapse.artifacts import ArtifactsClient

logger = logging.getLogger(__name__)


def arguments_loader(desc: str):
    parser = argparse.ArgumentParser(description=desc)
    parser.add_argument('--db-path', type=str, required=True, help='Path to DuckDB database file')
    parser.add_argument(
        '--credential-config-path', type=str, required=True, help='Path string containing credential configuration'
    )
    args = parser.parse_args()
    credential_file = args.credential_config_path

    if not credential_file.endswith('credentials.yml'):
        msg = "Credential config file must have 'credentials.yml' extension"
        # This is the output format expected by the pipeline.py which orchestrates the execution of this script
        print(json.dumps({"status": "error", "message": msg}), file=sys.stderr)
        raise ValueError("Credential config file must have 'credentials.yml' extension")

    return args.db_path, credential_file


def insert_df_to_duckdb(df: pd.DataFrame, db_path: str, table_name: str) -> None:
    """
    Insert a pandas DataFrame into a DuckDB table.

    Args:
        df (pd.DataFrame): The pandas DataFrame to insert
        db_path (str): Path to the DuckDB database file
        table_name (str): Name of the table to insert data into
    """
    try:
        # Connect to DuckDB
        conn = duckdb.connect(db_path)

        # Drop existing table if it exists
        conn.execute(f"DROP TABLE IF EXISTS {table_name}")

        if df.empty:
            # If DataFrame is empty, create an empty table with the correct schema
            if len(df.columns) > 0:
                # Create empty table with the same schema
                conn.execute(f"CREATE TABLE {table_name} AS SELECT * FROM df LIMIT 0")
                logging.info(f"Created empty table {table_name} with schema: {df.columns.tolist()}")
                print(f"Created empty table {table_name} with schema: {df.columns.tolist()}")
            else:
                logging.warning(f"Skipping table {table_name} creation as DataFrame has no columns")
            conn.close()
            return
        # Create the table with the DataFrame's schema and insert data
        conn.execute(f"CREATE TABLE {table_name} AS SELECT * FROM df")
        logging.info(f"Successfully inserted {len(df)} rows into {table_name} table")
        print(f"Successfully inserted {len(df)} rows into {table_name} table")

        # Close connection
        conn.close()
    except Exception as e:
        logging.error(f"Error inserting data into DuckDB: {str(e)}")
        raise


def get_config(creds_file: str) -> dict:
    """
    Load the configuration from a YAML file.

    Args:
        creds_file (str): Path to the YAML file

    Returns:
        dict: Configuration dictionary
    """
    try:
        with open(creds_file, 'r') as file:
            config = yaml.safe_load(file)
        return config
    except Exception as e:
        logging.error(f"Error loading configuration file: {str(e)}")
        raise


def get_synapse_artifacts_client(config: dict) -> ArtifactsClient:
    """
    :return:  an Azure SDK client handle for Synapse Artifacts
    """
    return ArtifactsClient(
        endpoint=config["azure_api_access"]["development_endpoint"], credential=DefaultAzureCredential()
    )


def get_synapse_jdbc_settings(config: dict):
    """
    Get Synapse JDBC settings from the configuration.

    Args:
            config (dict): Configuration dictionary

    Returns:
            dict: Synapse JDBC settings
    """

    synapse_jdbc_sql_authentication = {
        "dedicated_sqlpool_url_template": "mssql+pyodbc://{user}:{pwd}@{endpoint}:1433/database={database}?driver={driver};encrypt=true;trustServerCertificate=false;hostNameInCertificate=*.sql.azuresynapse.net;loginTimeout=30;",
        "serverless_sqlpool_url_template": "jdbc://{endpoint}:1433;encrypt=true;trustServerCertificate=false;hostNameInCertificate=*.sql.azuresynapse.net;loginTimeout=30;",
        "fetch_size": "1000",
    }
    synapse_jdbc_ad_passwd_authentication = {
        "dedicated_sqlpool_url_template": "mssql+pyodbc://{endpoint}:1433;database={database};encrypt=true;trustServerCertificate=false;hostNameInCertificate=*.sql.azuresynapse.net;loginTimeout=30;authentication=ActiveDirectoryPassword",
        "serverless_sqlpool_url_template": "mssql+pyodbc://{endpoint}:1433;encrypt=true;trustServerCertificate=false;hostNameInCertificate=*.sql.azuresynapse.net;loginTimeout=30;authentication=ActiveDirectoryPassword",
        "fetch_size": "1000",
    }
    synapse_jdbc_spn_authentication = {
        "dedicated_sqlpool_url_template": "mssql+pyodbc://{endpoint}:1433;database={database};encrypt=true;trustServerCertificate=false;hostNameInCertificate=*.sql.azuresynapse.net;loginTimeout=30;authentication=ActiveDirectoryServicePrincipal",
        "serverless_sqlpool_url_template": "mssql+pyodbc://{endpoint}:1433;encrypt=true;trustServerCertificate=false;hostNameInCertificate=*.sql.azuresynapse.net;loginTimeout=30;authentication=ActiveDirectoryServicePrincipal",
        "fetch_size": "1000",
    }

    auth_type = config.get("jdbc").get("auth_type")
    if auth_type == "sql_authentication":
        return synapse_jdbc_sql_authentication
    elif auth_type == "ad_password_authentication":
        return synapse_jdbc_ad_passwd_authentication
    elif auth_type == "spn_authentication":
        return synapse_jdbc_spn_authentication
    else:
        raise ValueError(f"Unsupported authentication type: {auth_type}")


def get_azure_metrics_query_client():
    """
    :return: an Azure SDK Monitoring Metrics Client handle
    """
    return MetricsQueryClient(credential=DefaultAzureCredential())


def save_resultset_to_db(result, table_name: str, db_path: str, mode: str, batch_size: int = 1000):
    try:
        with duckdb.connect(db_path) as conn:
            columns = result.keys()
            schema = ', '.join([f"{col} STRING" for col in columns])
            print(f"schema: {schema} for table: {table_name}")

            # Fetch the first batch
            first_rows = result.fetchmany(batch_size)
            print(first_rows)
            if not first_rows:
                logging.info(f"No data to save for table {table_name}.")
                return

            # Handle write modes
            if mode == 'overwrite':
                conn.execute(f"CREATE OR REPLACE TABLE {table_name} ({schema})")
            elif mode == 'append' and table_name not in conn.get_table_names(""):
                conn.execute(f"CREATE TABLE IF NOT EXISTS {table_name} ({schema})")
            print("Tables created *********")

            # Batch insert using prepared statements
            placeholders = ', '.join(['?' for _ in columns])
            insert_query = f"INSERT INTO {table_name} VALUES ({placeholders})"

            # Insert the first batch
            conn.executemany(insert_query, first_rows)

            # Continue with the rest
            while True:
                rows = result.fetchmany(batch_size)
                if not rows:
                    break
                conn.executemany(insert_query, rows)
    except Exception as e:
        logging.error(f"Error in save_resultset_to_db for table {table_name}: {str(e)}")
        print(f"ERROR: save_resultset_to_db for table {table_name}: {e}")


def get_max_column_value_duckdb(column_name, table_name, db_path):
    """
    Get the maximum value of a column from a DuckDB table.
    """
    try:
        conn = duckdb.connect(db_path)
        # Check if table exists
        table_exists = table_name in conn.execute("SHOW TABLES").fetchdf()['name'].values
        if not table_exists:
            print(f"INFO: Table {table_name} does not exist in DuckDB. Returning None.")
            conn.close()
            return None
        max_column_query = f"SELECT MAX({column_name}) AS last_{column_name} FROM {table_name}"
        print(f"INFO: get_max_column_value_duckdb:: query {max_column_query}")
        rows = conn.execute(max_column_query).fetchall()
        max_column_val = rows[0][0] if rows else None
        conn.close()
    except Exception as e:
        print(f"ERROR: {e}")
    print(f"INFO: max_column_val = {max_column_val}")
    return max_column_val


def get_serverless_database_groups(
    db_path, inclusion_list=None, exclusion_list=None, table_name="serverless_databases"
):
    """
    Reads serverless databases from DuckDB, groups by collation_name,
    and applies inclusion/exclusion filters.

    Returns:
        (serverless_database_groups, serverless_database_groups_in_scope)
    """
    rows = duckdb.connect(db_path).execute(f"SELECT name, collation_name FROM {table_name}").fetchall()

    serverless_database_groups = {}
    for name, collation_name in rows:
        serverless_database_groups.setdefault(collation_name, []).append(name)

    inclusion_list = inclusion_list or []
    exclusion_list = exclusion_list or []

    serverless_database_groups_in_scope = {}
    for collation_name, dbs in serverless_database_groups.items():
        for db in dbs:
            if (not inclusion_list or db in inclusion_list) and db not in exclusion_list:
                serverless_database_groups_in_scope.setdefault(collation_name, []).append(db)

    return serverless_database_groups_in_scope
