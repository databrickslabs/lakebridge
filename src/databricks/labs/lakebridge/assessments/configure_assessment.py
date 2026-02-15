from abc import ABC, abstractmethod
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


class ConfigureSqlServerAssessment(AssessmentConfigurator):
    """SQL Server specific assessment configuration."""

    def _configure_credentials(self) -> str:
        cred_file = self._credential_file
        source = self._source_name

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
                "password": self.prompts.password("Enter the password details"),
            },
        }

        _save_to_disk(credential, cred_file)
        logger.info(f"Credential template created for {source}.")
        return source


# Redshift auth methods (connection still via SQLAlchemy + user/password from config; no boto3).
REDSHIFT_AUTH_METHODS = [
    "database_password",
    "temporary_credentials_db_user",
    "temporary_credentials_iam",
    "federated_user",
    "secrets_manager",
]

REDSHIFT_CREDENTIAL_SOURCES = ["local", "env", "file"]


class ConfigureRedshiftAssessment(AssessmentConfigurator):
    """Redshift specific assessment configuration."""

    def _configure_credentials(self) -> str:
        cred_file = self._credential_file
        source = self._source_name

        logger.info(
            "Redshift authentication: database_password, temporary_credentials_db_user, "
            "temporary_credentials_iam, federated_user, or secrets_manager. "
            "Credentials are provided via local (plain text in file), env (environment variables), "
            "or file (use existing credential file if valid else prompt)."
        )
        auth_method = str(
            self.prompts.choice("Authentication method", REDSHIFT_AUTH_METHODS)
        ).lower()
        choice = str(
            self.prompts.choice("Credential source (local | env | file)", REDSHIFT_CREDENTIAL_SOURCES)
        ).lower()
        if choice == "file":
            if cred_file.exists():
                try:
                    with open(cred_file, encoding="utf-8") as f:
                        data = yaml.safe_load(f)
                except (yaml.YAMLError, OSError):
                    data = None
                source_creds = data.get(source) if data and isinstance(data, dict) else None
                required = ["host", "port", "database", "user", "password"]
                if source_creds and isinstance(source_creds, dict) and all(k in source_creds for k in required):
                    logger.info(f"Using existing credential file at {cred_file}.")
                    return source
            logger.info("Credential file not found or incomplete, prompting for connection details.")
            choice = "local"
        secret_vault_type = choice
        secret_vault_name = None

        logger.info("Please refer to the documentation to understand the difference between local and env.")

        credential = {
            "secret_vault_type": secret_vault_type,
            "secret_vault_name": secret_vault_name,
            source: {
                "auth_method": auth_method,
                "host": self.prompts.question("Enter the Redshift cluster endpoint (host)"),
                "port": int(self.prompts.question("Enter the port details", valid_number=True, default="5439")),
                "database": self.prompts.question("Enter the database name"),
                "user": self.prompts.question("Enter the user details"),
                "password": self.prompts.password("Enter the password details"),
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


def create_assessment_configurator(
    source_system: str, product_name: str, prompts: Prompts, credential_file=None
) -> AssessmentConfigurator:
    """Factory function to create the appropriate assessment configurator."""
    configurators = {
        "mssql": ConfigureSqlServerAssessment,
        "redshift": ConfigureRedshiftAssessment,
        "synapse": ConfigureSynapseAssessment,
    }

    if source_system not in configurators:
        raise ValueError(f"Unsupported source system: {source_system}")

    return configurators[source_system](product_name, prompts, source_system, credential_file)
