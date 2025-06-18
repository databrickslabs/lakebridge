from abc import ABC, abstractmethod
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

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

PROFILER_SOURCE_SYSTEM = ["mssql", "synapse"]


class AssessmentConfigurator(ABC):
    """Abstract base class for assessment configuration."""

    def __init__(self, product_name: str, prompts: Prompts, credential_file=None):
        self.prompts = prompts
        self._product_name = product_name
        self._credential_file = creds(product_name) if not credential_file else credential_file

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
        if self.prompts.confirm(f"Do you want to test the connection to {source}?"):
            cred_manager = create_credential_manager("lakebridge", EnvGetter())
            if cred_manager:
                self._test_connection(source, cred_manager)
        logger.info(f"{source.capitalize()} Assessment Configuration Completed")


class ConfigureSqlServerAssessment(AssessmentConfigurator):
    """SQL Server specific assessment configuration."""

    def _configure_credentials(self) -> str:
        cred_file = self._credential_file
        source = "mssql"

        logger.info(
            "\n(local | env) \nlocal means values are read as plain text \nenv means values are read "
            "from environment variables fall back to plain text if not variable is not found\n",
        )
        secret_vault_type = str(self.prompts.choice("Enter secret vault type (local | env)", ["local", "env"])).lower()
        secret_vault_name = None

        logger.info("Please refer to the documentation to understand the difference between local and env.")

        credential = {
            "secret_vault_type": secret_vault_type,
            "secret_vault_name": secret_vault_name,
            source: {
                "database": self.prompts.question("Enter the database name"),
                "driver": self.prompts.question("Enter the driver details"),
                "server": self.prompts.question("Enter the server or host details"),
                "port": int(self.prompts.question("Enter the port details", valid_number=True)),
                "user": self.prompts.question("Enter the user details"),
                "password": self.prompts.question("Enter the password details"),
            },
        }

        if cred_file.exists():
            backup_filename = cred_file.with_suffix('.bak')
            shutil.copy(cred_file, backup_filename)
            logger.debug(f"Backup of the existing file created at {backup_filename}")

        with open(cred_file, 'w', encoding='utf-8') as file:
            yaml.dump(credential, file, default_flow_style=False)

        logger.info(f"Credential template created for {source}.")
        return source


class ConfigureSynapseAssessment(AssessmentConfigurator):
    """Synapse specific assessment configuration."""

    def _configure_credentials(self) -> str:
        cred_file = self._credential_file
        source = "synapse"

        logger.info(
            "\n(local | env) \nlocal means values are read as plain text \nenv means values are read "
            "from environment variables fall back to plain text if not variable is not found\n",
        )
        secret_vault_type = str(self.prompts.choice("Enter secret vault type (local | env)", ["local", "env"])).lower()
        secret_vault_name = None

        # Synapse Workspace Settings
        logger.info("Please provide Synapse Workspace settings:")
        synapse_workspace = {
            "name": self.prompts.question("Enter Synapse workspace name"),
            "dedicated_sql_endpoint": self.prompts.question("Enter dedicated SQL endpoint"),
            "serverless_sql_endpoint": self.prompts.question("Enter serverless SQL endpoint"),
            "sql_user": self.prompts.question("Enter SQL user"),
            "sql_password": self.prompts.question("Enter SQL password"),
            "tz_info": self.prompts.question("Enter timezone (e.g. America/New_York)", default="UTC"),
        }

        # Azure API Access Settings
        logger.info("Please provide Azure API access settings:")
        azure_api_access = {
            "development_endpoint": self.prompts.question("Enter development endpoint"),
            "azure_client_id": self.prompts.question("Enter Azure client ID"),
            "azure_tenant_id": self.prompts.question("Enter Azure tenant ID"),
            "azure_client_secret": self.prompts.question("Enter Azure client secret"),
        }

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

        if cred_file.exists():
            backup_filename = cred_file.with_suffix('.bak')
            shutil.copy(cred_file, backup_filename)
            logger.debug(f"Backup of the existing file created at {backup_filename}")

        with open(cred_file, 'w', encoding='utf-8') as file:
            yaml.dump(credential, file, default_flow_style=False)

        logger.info(f"Credential template created for {source}.")
        return source


def create_assessment_configurator(
    source_system: str, product_name: str, prompts: Prompts, credential_file=None
) -> AssessmentConfigurator:
    """Factory function to create the appropriate assessment configurator."""
    configurators = {
        "mssql": ConfigureSqlServerAssessment,
        "synapse": ConfigureSynapseAssessment,
    }

    if source_system not in configurators:
        raise ValueError(f"Unsupported source system: {source_system}")

    return configurators[source_system](product_name, prompts, credential_file)
