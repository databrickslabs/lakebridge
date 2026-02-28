import logging
from databricks.labs.lakebridge.connections.database_manager import DatabaseManager

logger = logging.getLogger(__name__)


def create_synapse_connection(
    workspace_config: dict,
    database: str,
    endpoint_key: str = 'dedicated_sql_endpoint',
    auth_type: str = 'sql_authentication',
) -> DatabaseManager:
    """Create a DatabaseManager connection to a Synapse SQL pool.

    Transforms Synapse workspace configuration (with sql_user/sql_password) into
    the standard DatabaseManager format (with user/password).

    Returns:
        DatabaseManager configured for the specified Synapse SQL pool

    """
    server = workspace_config.get(endpoint_key)
    if not server:
        raise ValueError(f"Endpoint '{endpoint_key}' not found in workspace config")

    config = {
        "driver": workspace_config['driver'],
        "server": server,
        "database": database,
        "user": workspace_config['sql_user'],
        "password": workspace_config['sql_password'],
        "port": workspace_config.get('port', 1433),
        "auth_type": auth_type,
    }

    return DatabaseManager(db_type="synapse", config=config)


def _test_pool_connection(
    pool_name: str,
    workspace_config: dict,
    database: str,
    endpoint_key: str,
    auth_type: str,
) -> tuple[bool, str | None]:
    """Test connection to a single Synapse SQL pool with automatic resource cleanup.

    Args:
        pool_name: Name of the pool for logging (e.g., "dedicated", "serverless")
        workspace_config: Synapse workspace configuration
        database: Database name to connect to
        endpoint_key: Key in workspace_config for the endpoint URL
        auth_type: Authentication type

    Returns:
        Tuple of (success, error_message). error_message is None if successful.
    """
    logger.info(f"Testing connection to {pool_name} SQL pool...")

    try:
        with create_synapse_connection(workspace_config, database, endpoint_key, auth_type) as db_manager:
            if db_manager.check_connection():
                logger.info(f"✓ {pool_name.capitalize()} SQL pool connection successful")
                return True, None
            logger.error(f"✗ {pool_name.capitalize()} SQL pool connection failed")
            return False, f"{pool_name.capitalize()} SQL pool connection check failed"
    except Exception as e:  # pylint: disable=broad-exception-caught
        # Catch all exceptions to gracefully handle any connection failure (network, auth, config, etc.)
        error_msg = f"Failed to connect to {pool_name} SQL pool: {e}"
        logger.error(f"✗ {error_msg}")
        return False, error_msg


def validate_synapse_pools(raw_config: dict) -> None:
    """
    Validate connections to enabled Synapse SQL pools based on profiler configuration.
    Each connection is properly cleaned up after testing to prevent resource leaks.

    Example:
        >>> config = {
        ...     'workspace': {
        ...         'dedicated_sql_endpoint': 'workspace.sql.azuresynapse.net',
        ...         'serverless_sql_endpoint': 'workspace-ondemand.sql.azuresynapse.net',
        ...         'sql_user': 'admin',
        ...         'sql_password': 'pass',
        ...         'driver': 'ODBC Driver 18 for SQL Server',
        ...     },
        ...     'jdbc': {'auth_type': 'sql_authentication'},
        ...     'profiler': {'exclude_serverless_sql_pool': False},
        ... }
        >>> validate_synapse_pools(config)  # Tests both pools
    """
    workspace_config = raw_config.get("workspace", {})
    jdbc_config = raw_config.get("jdbc", {})
    profiler_config = raw_config.get("profiler", {})

    auth_type = jdbc_config.get("auth_type", "sql_authentication")
    database = "master"

    # Determine which pools to test
    test_dedicated = not profiler_config.get("exclude_dedicated_sql_pools", False)
    test_serverless = not profiler_config.get("exclude_serverless_sql_pool", False)

    if not test_dedicated and not test_serverless:
        logger.warning("Both dedicated and serverless SQL pools are excluded in profiler configuration")
        raise ValueError("No SQL pools enabled for testing")

    # Track results and error messages
    results = {}
    error_messages = {}

    # Test enabled pools sequentially
    if test_dedicated:
        success, error_msg = _test_pool_connection(
            "dedicated", workspace_config, database, "dedicated_sql_endpoint", auth_type
        )
        results["dedicated"] = success
        if error_msg:
            error_messages["dedicated"] = error_msg

    if test_serverless:
        success, error_msg = _test_pool_connection(
            "serverless", workspace_config, database, "serverless_sql_endpoint", auth_type
        )
        results["serverless"] = success
        if error_msg:
            error_messages["serverless"] = error_msg

    # Check if any pools failed
    failed_pools = [pool for pool, success in results.items() if not success]
    if failed_pools:
        error_details = "; ".join([f"{pool}: {error_messages.get(pool, 'Unknown error')}" for pool in failed_pools])
        raise ConnectionError(f"Connection failed for SQL pools - {error_details}")
