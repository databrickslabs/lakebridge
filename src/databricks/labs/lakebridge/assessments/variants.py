"""Profiler variant resolution.

A source's ``SOURCE_SYSTEM_VARIANTS`` entry carries the ``AUTO`` marker when the variant is
auto-detected here from a live connection. Sources absent from the registry have no variant.
"""

import logging
from collections.abc import Callable
from pathlib import Path

from databricks.labs.lakebridge.assessments import SOURCE_SYSTEM_VARIANTS, AUTO
from databricks.labs.lakebridge.connections.database_manager import create_connector, ALL_DATABASES
from databricks.labs.lakebridge.connections.credential_manager import create_credential_manager
from databricks.labs.lakebridge.connections.env_getter import EnvGetter

logger = logging.getLogger(__name__)


# Azure SQL Database is the only single-database SQL Server edition. On-prem SQL Server and Azure SQL
# Managed Instance host multiple databases per instance and use the multi-database profiler variant.
SQLSERVER_AZURE_SQL_DB_ENGINE_EDITION = 5


def resolve_mssql_variant(cred_file_path: Path | None) -> str:
    """Pick the SQL Server profiler variant from the configured database and the server edition.

    A specific database scopes profiling to just that database (``single_db``) on any edition. A blank value
    or the ``ALL_DATABASES`` (``*``) sentinel means "all": the edition then decides — on-prem SQL Server and
    Azure SQL Managed Instance profile every database (``multi_db``). Azure SQL Database is one database per
    connection with no cross-database access, so "all" is not meaningful there: it requires a concrete
    database name (otherwise the connection falls back to ``master`` and profiles the wrong database).
    """
    cred_manager = create_credential_manager("mssql", EnvGetter(), creds_path=cred_file_path)
    connect_config = cred_manager.get_credentials("mssql")
    database = str(connect_config.get("database") or "").strip()
    if database and database != ALL_DATABASES:
        # The user picked a specific database -> profile only that one, regardless of edition.
        return "single_db"
    with create_connector("mssql", connect_config) as connector:
        # SERVERPROPERTY returns sql_variant, which pyodbc cannot fetch (ODBC type -16); CAST to int.
        result = connector.fetch("SELECT CAST(SERVERPROPERTY('EngineEdition') AS INT) AS engine_edition")
    engine_edition = int(result.rows[0][0])
    if engine_edition == SQLSERVER_AZURE_SQL_DB_ENGINE_EDITION:
        raise ValueError(
            "Azure SQL Database profiles a single database per connection and does not support "
            "'*'/all-databases. Set a concrete database name in the mssql credentials."
        )
    logger.info(f"Detected SQL Server EngineEdition={engine_edition}; using 'multi_db' profiler variant")
    return "multi_db"


Resolvers = dict[str, Callable[[Path | None], str]]

# Sources whose variant is auto-detected from a live connection (probe) rather than supplied explicitly.
VARIANT_RESOLVERS: Resolvers = {
    "mssql": resolve_mssql_variant,
}


def resolve_variant(
    source_system: str,
    variant: str | None,
    *,
    resolvers: Resolvers | None = None,
    cred_file_path: Path | None = None,
) -> str | None:
    """Resolve the effective pipeline variant for a source.

    Sources absent from ``SOURCE_SYSTEM_VARIANTS`` have no variant. Sources marked ``AUTO`` probe a
    live connection via the registered resolver. Explicit ``variant`` input is ignored with a warning
    in both cases.
    """
    resolvers = resolvers if resolvers else VARIANT_RESOLVERS
    spec = SOURCE_SYSTEM_VARIANTS.get(source_system)
    if spec is None:
        if variant:
            logger.warning(f"Ignoring variant '{variant}': source system '{source_system}' has no variants.")
        return None

    if AUTO not in spec:
        raise ValueError(f"Source '{source_system}' has unsupported variant spec {spec}; only AUTO is supported.")

    if variant and variant != AUTO:
        logger.warning(f"Ignoring variant '{variant}'. Auto-detecting for source system '{source_system}'.")
    resolver = resolvers.get(source_system)
    if resolver is None:
        raise ValueError(f"Source '{source_system}' is marked auto-detect but has no registered resolver.")
    return resolver(cred_file_path)
