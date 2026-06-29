"""Profiler variant resolution.

A source's ``SOURCE_SYSTEM_VARIANTS`` entry is either a tuple of explicit choices (the CLI prompts the
user for one) or carries the ``AUTO`` marker (the variant is auto-detected here from a live connection).
This module owns the execute-time resolution; prompting stays in the CLI.
"""

import logging
from collections.abc import Callable
from pathlib import Path

from databricks.labs.lakebridge.assessments import SOURCE_SYSTEM_VARIANTS, AUTO
from databricks.labs.lakebridge.connections.database_manager import DatabaseManager, ALL_DATABASES
from databricks.labs.lakebridge.connections.credential_manager import create_credential_manager
from databricks.labs.lakebridge.connections.env_getter import EnvGetter

logger = logging.getLogger(__name__)


# Azure SQL Database is the only single-database SQL Server edition. On-prem SQL Server and Azure SQL
# Managed Instance host multiple databases per instance and use the multi-database profiler variant.
SQLSERVER_AZURE_SQL_DB_ENGINE_EDITION = 5


def resolve_mssql_variant(cred_file_path: Path | None) -> str:
    """Pick the SQL Server profiler variant from the configured database and the server edition.

    A specific database scopes profiling to just that database (``single_db``) on any edition. A blank value
    or the ``ALL_DATABASES`` (``*``) sentinel means "all": the edition then decides — Azure SQL Database falls
    back to the connected database (``single_db``); on-prem SQL Server and Azure SQL Managed Instance profile
    every database (``multi_db``).
    """
    cred_manager = create_credential_manager("mssql", EnvGetter(), creds_path=cred_file_path)
    connect_config = cred_manager.get_credentials("mssql")
    database = str(connect_config.get("database") or "").strip()
    if database and database != ALL_DATABASES:
        # The user picked a specific database -> profile only that one, regardless of edition.
        return "single_db"
    with DatabaseManager("mssql", connect_config) as db_manager:
        # SERVERPROPERTY returns sql_variant, which pyodbc cannot fetch (ODBC type -16); CAST to int.
        result = db_manager.fetch("SELECT CAST(SERVERPROPERTY('EngineEdition') AS INT) AS engine_edition")
    engine_edition = int(result.rows[0][0])
    variant = "single_db" if engine_edition == SQLSERVER_AZURE_SQL_DB_ENGINE_EDITION else "multi_db"
    logger.info(f"Detected SQL Server EngineEdition={engine_edition}; using '{variant}' profiler variant")
    return variant


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

    The ``SOURCE_SYSTEM_VARIANTS`` entry is either a tuple of explicit choices (the CLI prompts for one of
    these) or the ``AUTO`` marker (the variant is probed from a live connection here). ``variant`` is an
    explicit choice, the ``AUTO`` sentinel, or ``None``.
    """
    resolvers = resolvers if resolvers else VARIANT_RESOLVERS
    spec = SOURCE_SYSTEM_VARIANTS.get(source_system)
    if spec is None:
        if variant:
            logger.warning(f"Ignoring variant '{variant}': source system '{source_system}' has no variants.")
        return None

    if AUTO in spec:
        if variant and variant != AUTO:
            logger.warning(f"Ignoring variant '{variant}'. Auto-detecting for source system '{source_system}'.")
        resolver = resolvers.get(source_system)
        if resolver is None:
            raise ValueError(f"Source '{source_system}' is marked auto-detect but has no registered resolver.")
        return resolver(cred_file_path)

    if not variant:
        raise ValueError(f"No variant selected for '{source_system}' (choices: {spec}); the CLI must prompt for one.")

    chosen = variant.lower()
    if chosen not in spec:
        raise ValueError(f"Invalid variant '{chosen}' for '{source_system}'. Valid variants: {spec}.")
    return chosen
