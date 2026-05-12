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


class ConfigureSqlServerAssessment(AssessmentConfigurator):
    """SQL Server-family assessment configuration.

    Used for both `mssql` (regular SQL Server / Azure SQL Database, default database `master`)
    and `legacy_synapse` (Azure Synapse dedicated SQL pool, where the database
    is the pool name and must be supplied explicitly).
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
                "database": self.prompts.question("Enter the database name", default="master"),
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


ConfiguratorFactory = Callable[[str, Prompts, str, Path | str | None], AssessmentConfigurator]


class ConfigureBigQueryAssessment(AssessmentConfigurator):
    """BigQuery specific assessment configuration."""

    def _configure_credentials(self) -> str:
        cred_file = self._credential_file
        source = self._source_name

        logger.info(
            "\n(local | env) \nlocal means values are read as plain text \nenv means values are read "
            "from environment variables fall back to plain text if not variable is not found\n",
        )
        secret_vault_type = str(self.prompts.choice("Enter secret vault type (local | env)", ["local", "env"])).lower()
        secret_vault_name = None

        logger.info("Please provide BigQuery connection settings:")
        projects_raw = self.prompts.question("Enter BigQuery project IDs (comma-separated)")
        projects = [p.strip() for p in projects_raw.split(",") if p.strip()]

        regions_raw = self.prompts.question("Enter BigQuery regions (comma-separated, e.g. us, eu)", default="us")
        regions = [r.strip() for r in regions_raw.split(",") if r.strip()]

        sa_key_path = self.prompts.question(
            "Enter path to service account JSON key (leave blank to use Application Default Credentials)",
            default="",
        )
        service_account_key_path = sa_key_path.strip() or None

        profiling_window_days = int(
            self.prompts.question("Enter profiling window in days", default="180", valid_number=True)
        )
        max_parallel_sqls = int(
            self.prompts.question(
                "Enter max parallel SQLs per (project, region) iteration", default="8", valid_number=True
            )
        )

        logger.info("Please select target Databricks platform:")
        target_cloud = str(self.prompts.choice("Select target Databricks platform", ["aws", "azure", "gcp"])).lower()

        logger.info("Please configure profiler settings:")
        bigquery_profiler = {
            "redact_query_text": self.prompts.confirm("Redact query text in extracted data?"),
            "exclude_reservations_data": self.prompts.confirm("Exclude reservations and commitments data?"),
            "exclude_streaming_metrics": self.prompts.confirm("Exclude streaming and write API summary?"),
            "exclude_pricing_analysis": self.prompts.confirm("Exclude pricing analysis (skip step 2)?"),
        }

        credential = {
            "secret_vault_type": secret_vault_type,
            "secret_vault_name": secret_vault_name,
            source: {
                "projects": projects,
                "regions": regions,
                "service_account_key_path": service_account_key_path,
                "profiling_window_days": profiling_window_days,
                "target_cloud": target_cloud,
                "max_parallel_sqls": max_parallel_sqls,
                "profiler": bigquery_profiler,
            },
        }
        _save_to_disk(credential, cred_file)

        logger.info(f"Credential template created for {source}.")
        return source


def create_assessment_configurator(
    source_system: str, product_name: str, prompts: Prompts, credential_file: Path | str | None = None
) -> AssessmentConfigurator:
    """Factory function to create the appropriate assessment configurator."""
    configurators: dict[str, ConfiguratorFactory] = {
        "mssql": ConfigureSqlServerAssessment,
        "synapse": ConfigureSynapseAssessment,
        "legacy_synapse": ConfigureSqlServerAssessment,
        "bigquery": ConfigureBigQueryAssessment,
    }

    if source_system not in configurators:
        raise ValueError(f"Unsupported source system: {source_system}")

    return configurators[source_system](product_name, prompts, source_system, credential_file)  # type: ignore[abstract]
