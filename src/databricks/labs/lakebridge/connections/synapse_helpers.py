import logging
from databricks.labs.lakebridge.connections.database_manager import DatabaseManager

logger = logging.getLogger(__name__)


def _test_pool_connection(pool_name: str, base_config: dict, endpoint_key: str) -> tuple[bool, str | None]:
    """Test connection to a single Synapse SQL pool with proper resource cleanup.

    Returns:
        Tuple of (success, error_message). error_message is None if successful.
    """
    logger.info(f"Testing connection to {pool_name} SQL pool...")
    pool_config = {**base_config, "endpoint_key": endpoint_key}
    db_manager = None

    try:
        db_manager = DatabaseManager("synapse", pool_config)
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
    finally:
        # Clean up database engine resources
        if db_manager and hasattr(db_manager, 'connector') and hasattr(db_manager.connector, 'engine'):
            db_manager.connector.engine.dispose()
            logger.debug(f"Disposed engine for {pool_name} SQL pool")


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

    # Build base config shared by all pools
    base_config = {
        **workspace_config,
        "database": "master",
        "auth_type": auth_type,
    }

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
        success, error_msg = _test_pool_connection("dedicated", base_config, "dedicated_sql_endpoint")
        results["dedicated"] = success
        if error_msg:
            error_messages["dedicated"] = error_msg

    if test_serverless:
        success, error_msg = _test_pool_connection("serverless", base_config, "serverless_sql_endpoint")
        results["serverless"] = success
        if error_msg:
            error_messages["serverless"] = error_msg

    # Check if any pools failed
    if not all(results.values()):
        failed_pools = [pool for pool, success in results.items() if not success]
        error_details = "; ".join([f"{pool}: {error_messages.get(pool, 'Unknown error')}" for pool in failed_pools])
        raise ConnectionError(f"Connection failed for SQL pools - {error_details}")
