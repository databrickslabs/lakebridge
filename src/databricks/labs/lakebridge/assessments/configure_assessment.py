from abc import ABC, abstractmethod
from collections.abc import Callable
from pathlib import Path
import logging
import shutil
import yaml

from databricks.labs.blueprint.tui import Prompts

from databricks.labs.lakebridge.connections.credential_manager import (
    cred_file as creds,
    CredentialManager,
    create_credential_manager,
)
from databricks.labs.lakebridge.connections.database_manager import DatabaseManager
from databricks.labs.lakebridge.connections.env_getter import EnvGetter
from databricks.labs.lakebridge.assessments import CONNECTOR_REQUIRED

logger = logging.getLogger(__name__)


def _save_to_disk(credential: dict, cred_file: Path) -> None:
    if cred_file.exists():
        backup_filename = cred_file.with_suffix('.bak')
        shutil.copy(cred_file, backup_filename)
        logger.debug(f"Backup of the existing file created at {backup_filename}")

    with open(cred_file, 'w', encoding='utf-8') as file:
        yaml.dump(credential, file, default_flow_style=False)


class AssessmentConfigurator(ABC):
    """Abstract base class for assessment configuration."""

    def __init__(
        self, product_name: str, prompts: Prompts, source_name: str, credential_file: Path | str | None = None
    ):
        self.prompts = prompts
        self._product_name = product_name
        self._credential_file = creds(product_name) if not credential_file else Path(credential_file)
        self._source_name = source_name

    @abstractmethod
    def _configure_credentials(self) -> str:
        pass

    @staticmethod
    def _test_connection(source: str, cred_manager: CredentialManager):
        config = cred_manager.get_credentials(source)

        try:
            db_manager = DatabaseManager(source, config)
            if db_manager.check_connection():
                logger.info("Connection to the source system successful")
            else:
                logger.error("Connection to the source system failed, check logs in debug mode")
                raise SystemExit("Connection validation failed. Exiting...")

        except ConnectionError as e:
            logger.error(f"Failed to connect to the source system: {e}")
            raise SystemExit("Connection validation failed. Exiting...") from e

    def run(self):
        """Run the assessment configuration process."""
        logger.info(f"Welcome to the {self._product_name} Assessment Configuration")
        source = self._configure_credentials()
        logger.info(f"{source.capitalize()} details and credentials received.")
        if CONNECTOR_REQUIRED.get(self._source_name, True):
            if self.prompts.confirm(f"Do you want to test the connection to {source}?"):
                cred_manager = create_credential_manager("lakebridge", EnvGetter())
                if cred_manager:
                    self._test_connection(source, cred_manager)
        logger.info(f"{source.capitalize()} Assessment Configuration Completed")


class ConfigureOracleAssessment(AssessmentConfigurator):
    """Oracle specific assessment configuration."""

    def _configure_credentials(self) -> str:
        cred_file = self._credential_file
        source = self._source_name

        logger.info(
            "\n(local | env) \nlocal means values are read as plain text \nenv means values are read "
            "from environment variables fall back to plain text if not variable is not found\n",
        )
        secret_vault_type = str(self.prompts.choice("Enter secret vault type (local | env)", ["local", "env"])).lower()

        credential = {
            "secret_vault_type": secret_vault_type,
            source: {
                "host": self.prompts.question("Enter the host details (Server name, IP address, SCAN Name)"),
                "port": int(self.prompts.question("Enter the host port number", default=str(1521), valid_number=True)),
                "service_name": self.prompts.question("Enter the service name", default="orcl"),
                "user": self.prompts.question("Enter user with privileges"),
                "password": self.prompts.password("Enter user password"),
            },
        }

        _save_to_disk(credential, cred_file)
        logger.info(f"Credential template created for {source}.")
        return source


class ConfigureSqlServerAssessment(AssessmentConfigurator):
    """SQL Server-family assessment configuration.

    Used for both `mssql` (regular SQL Server / Azure SQL Database) and
    `legacy_synapse` (Azure Synapse dedicated SQL pool, where the database
    is the pool name).
    """

    def _configure_credentials(self) -> str:
        cred_file = self._credential_file
        source = self._source_name

        logger.info(
            "\n(local | env) \nlocal means values are read as plain text \nenv means values are read "
            "from environment variables fall back to plain text if not variable is not found\n",
        )
        secret_vault_type = str(self.prompts.choice("Enter secret vault type (local | env)", ["local", "env"])).lower()
        secret_vault_name = None

        credential = {
            "secret_vault_type": secret_vault_type,
            "secret_vault_name": secret_vault_name,
            source: {
                "auth_type": "sql_authentication",
                "fetch_size": self.prompts.question("Enter fetch size", default="1000"),
                "login_timeout": self.prompts.question("Enter login timeout (seconds)", default="30"),
                "server": self.prompts.question("Enter the fully-qualified server name"),
                "port": int(self.prompts.question("Enter the port details", valid_number=True)),
                "database": self.prompts.question("Enter the database name"),
                "user": self.prompts.question("Enter the SQL username"),
                "password": self.prompts.password("Enter the SQL password"),
                "tz_info": self.prompts.question("Enter timezone (e.g. America/New_York)", default="UTC"),
                "driver": self.prompts.question(
                    "Enter the ODBC driver installed locally", default="ODBC Driver 18 for SQL Server"
                ),
            },
        }

        _save_to_disk(credential, cred_file)
        logger.info(f"Credential template created for {source}.")
        return source


class ConfigureSynapseAssessment(AssessmentConfigurator):
    """Synapse specific assessment configuration."""

    def _configure_credentials(self) -> str:
        cred_file = self._credential_file
        source = self._source_name

        logger.info(
            "\n(local | env) \nlocal means values are read as plain text \nenv means values are read "
            "from environment variables fall back to plain text if not variable is not found\n",
        )
        secret_vault_type = str(self.prompts.choice("Enter secret vault type (local | env)", ["local", "env"])).lower()
        secret_vault_name = None

        # Synapse Workspace Settings
        logger.info("Please provide Synapse Workspace settings:")
        workspace_name = self.prompts.question("Enter Synapse workspace name")
        synapse_workspace = {
            "name": workspace_name,
            "dedicated_sql_endpoint": f"{workspace_name}.sql.azuresynapse.net",
            "serverless_sql_endpoint": f"{workspace_name}-ondemand.sql.azuresynapse.net",
            "sql_user": self.prompts.question("Enter SQL user"),
            "sql_password": self.prompts.password("Enter SQL password"),
            "tz_info": self.prompts.question("Enter timezone (e.g. America/New_York)", default="UTC"),
            "driver": self.prompts.question(
                "Enter the ODBC driver installed locally", default="ODBC Driver 18 for SQL Server"
            ),
        }

        # Azure API Access Settings
        logger.info("Please provide Azure access settings:")
        # Users use az cli to login to their Azure account and we just need the endpoint
        azure_api_access = {"development_endpoint": self.prompts.question("Enter development endpoint")}

        # JDBC Settings
        logger.info("Please select JDBC authentication type:")
        auth_type = self.prompts.choice(
            "Select authentication type", ["sql_authentication", "ad_passwd_authentication", "spn_authentication"]
        )

        synapse_jdbc = {
            "auth_type": auth_type,
            "fetch_size": self.prompts.question("Enter fetch size", default="1000"),
            "login_timeout": self.prompts.question("Enter login timeout (seconds)", default="30"),
        }

        # Profiler Settings
        logger.info("Please configure profiler settings:")
        synapse_profiler = {
            "exclude_serverless_sql_pool": self.prompts.confirm("Exclude serverless SQL pool from profiling?"),
            "exclude_dedicated_sql_pools": self.prompts.confirm("Exclude dedicated SQL pools from profiling?"),
            "exclude_spark_pools": self.prompts.confirm("Exclude Spark pools from profiling?"),
            "exclude_monitoring_metrics": self.prompts.confirm("Exclude monitoring metrics from profiling?"),
            "redact_sql_pools_sql_text": self.prompts.confirm("Redact SQL pools SQL text?"),
        }

        credential = {
            "secret_vault_type": secret_vault_type,
            "secret_vault_name": secret_vault_name,
            source: {
                "workspace": synapse_workspace,
                "azure_api_access": azure_api_access,
                "jdbc": synapse_jdbc,
                "profiler": synapse_profiler,
            },
        }
        _save_to_disk(credential, cred_file)

        logger.info(f"Credential template created for {source}.")
        return source


class ConfigureSnowflakeAssessment(AssessmentConfigurator):
    """Snowflake specific assessment configuration."""

    def _configure_credentials(self) -> str:
        cred_file = self._credential_file
        source = self._source_name

        logger.info(
            "\n(local | env) \nlocal means values are read as plain text \nenv means values are read "
            "from environment variables fall back to plain text if not variable is not found\n",
        )
        secret_vault_type = str(self.prompts.choice("Enter secret vault type (local | env)", ["local", "env"])).lower()

        # Snowflake Connection Settings
        logger.info("Snowflake Assessment Configuration")
        logger.info("Authentication uses a Programmatic Access Token (PAT). See Snowflake's docs:")
        logger.info(
            "  https://docs.snowflake.com/en/user-guide/programmatic-access-tokens"
            "#generating-a-programmatic-access-token"
        )

        # In env mode the stored value is the name of an environment variable that
        # EnvGetter resolves at runtime, not the token itself, so prompt accordingly.
        if secret_vault_type == "env":
            pat = self.prompts.question("Enter the environment variable name holding the PAT")
        else:
            pat = self.prompts.password("Enter Programmatic Access Token (PAT)")

        snowflake_connection = {
            "account": self.prompts.question(
                "Enter Snowflake account URL (e.g., myorg-myaccount.snowflakecomputing.com)"
            ),
            "user": self.prompts.question("Enter username"),
            "warehouse": self.prompts.question("Enter warehouse name", default="COMPUTE_WH"),
            "database": self.prompts.question("Enter database name", default="SNOWFLAKE"),
            "schema": self.prompts.question("Enter schema name", default="ACCOUNT_USAGE"),
            "role": self.prompts.question("Enter role", default="ACCOUNTADMIN"),
            # Stored under `pat` (not `password`) to flag this is a rotating
            # Programmatic Access Token, not a SQL password.
            "pat": pat,
        }

        credential = {
            "secret_vault_type": secret_vault_type,
            source: {
                "connection": snowflake_connection,
            },
        }
        _save_to_disk(credential, cred_file)

        logger.info(f"Credential template created for {source}.")
        return source


ConfiguratorFactory = Callable[[str, Prompts, str, Path | str | None], AssessmentConfigurator]


def create_assessment_configurator(
    source_system: str, product_name: str, prompts: Prompts, credential_file: Path | str | None = None
) -> AssessmentConfigurator:
    """Factory function to create the appropriate assessment configurator."""
    configurators: dict[str, ConfiguratorFactory] = {
        "mssql": ConfigureSqlServerAssessment,
        "synapse": ConfigureSynapseAssessment,
        "snowflake": ConfigureSnowflakeAssessment,
        "legacy_synapse": ConfigureSqlServerAssessment,
        "oracle": ConfigureOracleAssessment,
    }

    if source_system not in configurators:
        raise ValueError(f"Unsupported source system: {source_system}")

    return configurators[source_system](product_name, prompts, source_system, credential_file)
