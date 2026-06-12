from abc import ABC, abstractmethod
from collections.abc import Callable
from pathlib import Path
import logging
import os
import shutil
from typing import Any
import yaml

from databricks.labs.blueprint.tui import Prompts

from databricks.labs.lakebridge.connections.credential_manager import (
    cred_file as creds,
    CredentialManager,
    create_credential_manager,
)
from databricks.labs.lakebridge.connections.database_manager import DatabaseManager
from databricks.labs.lakebridge.connections.env_getter import EnvGetter
from databricks.labs.lakebridge.assessments import CONNECTOR_REQUIRED, source_system_family

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
    def _configure_credentials(self) -> None:
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
        self._configure_credentials()
        source = self._source_name
        logger.info(f"{source.capitalize()} details and credentials received.")
        if CONNECTOR_REQUIRED.get(self._source_name, True):
            if self.prompts.confirm(f"Do you want to test the connection to {source}?"):
                cred_manager = create_credential_manager("lakebridge", EnvGetter())
                if cred_manager:
                    self._test_connection(source, cred_manager)
        logger.info(f"{source.capitalize()} Assessment Configuration Completed")


class ConfigureOracleAssessment(AssessmentConfigurator):
    """Oracle specific assessment configuration."""

    def _configure_credentials(self) -> None:
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


class ConfigureSqlServerAssessment(AssessmentConfigurator):
    """SQL Server-family assessment configuration.

    Used for both `mssql` (regular SQL Server / Azure SQL Database) and
    `legacy_synapse` (Azure Synapse dedicated SQL pool, where the database
    is the pool name).
    """

    def _configure_credentials(self) -> None:
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
                "trust_server_certificate": self.prompts.confirm("Trust server certificate"),
                "tz_info": self.prompts.question("Enter timezone (e.g. America/New_York)", default="UTC"),
                "driver": self.prompts.question(
                    "Enter the ODBC driver installed locally", default="ODBC Driver 18 for SQL Server"
                ),
            },
        }

        _save_to_disk(credential, cred_file)
        logger.info(f"Credential template created for {source}.")


# Redshift auth types mirror the values ``RedshiftConnector._connect`` accepts. Keep the
# two lists in sync; if a new branch is added there, expose it here too.
REDSHIFT_AUTH_TYPES = ["sql_authentication", "iam"]

REDSHIFT_CREDENTIAL_SOURCES = ["local", "env", "file"]


class ConfigureRedshiftAssessment(AssessmentConfigurator):
    """Redshift specific assessment configuration."""

    def _prompt_iam_fields(self, source_creds: dict[str, Any]) -> None:
        """Prompt for the optional IAM extra-knob fields and write them only when set.

        ``redshift_connector`` resolves AWS credentials from the standard chain (env vars,
        ``~/.aws/credentials``, IAM instance profile); every field below is optional, and
        writing empty strings would poison the connector config, so empties are skipped.
        """
        fields = [
            (
                "db_user",
                "DB user to assume via GetClusterCredentials (leave empty to let IAM identity resolve)",
                "",
            ),
            (
                "cluster_identifier",
                "Cluster identifier (provisioned Redshift; leave empty for serverless or to auto-detect)",
                "",
            ),
            ("aws_profile", "AWS profile name (leave empty for default)", os.environ.get("AWS_PROFILE", "")),
            ("region", "AWS region (leave empty for default)", os.environ.get("AWS_REGION", "")),
        ]
        for key, prompt_text, default in fields:
            value = self.prompts.question(prompt_text, default=default)
            if value:
                source_creds[key] = value

    def _configure_credentials(self) -> None:
        cred_file = self._credential_file
        source = self._source_name

        logger.info(
            "Redshift authentication: sql_authentication (user/password) or iam (AWS IAM identity, "
            "credentials resolved from env/~/.aws/credentials/instance profile). "
            "Credentials are provided via local (plain text in file), env (environment variables), "
            "or file (use existing credential file if valid else prompt)."
        )
        auth_type = str(self.prompts.choice("Authentication type", REDSHIFT_AUTH_TYPES)).lower()
        choice = str(self.prompts.choice("Credential source (local | env | file)", REDSHIFT_CREDENTIAL_SOURCES)).lower()
        if choice == "file":
            if cred_file.exists():
                try:
                    with open(cred_file, encoding="utf-8") as f:
                        data = yaml.safe_load(f)
                except (yaml.YAMLError, OSError):
                    data = None
                existing_creds = data.get(source) if data and isinstance(data, dict) else None
                required = ["host", "port", "database"]
                if (existing_creds or {}).get("auth_type") == "sql_authentication":
                    required = required + ["user", "password"]
                if existing_creds and isinstance(existing_creds, dict) and all(k in existing_creds for k in required):
                    logger.info(f"Using existing credential file at {cred_file}.")
                    return

            logger.info("Credential file not found or incomplete, prompting for connection details.")
            choice = "local"
        secret_vault_type = choice
        secret_vault_name = None

        logger.info("Please refer to the documentation to understand the difference between local and env.")

        source_creds: dict[str, Any] = {"auth_type": auth_type, "ssl": "yes"}
        source_creds["host"] = self.prompts.question("Enter the Redshift cluster endpoint (host)")
        source_creds["port"] = int(self.prompts.question("Enter the port details", valid_number=True, default="5439"))
        source_creds["database"] = self.prompts.question("Enter the database name")
        if auth_type == "sql_authentication":
            source_creds["user"] = self.prompts.question("Enter the user details")
            source_creds["password"] = self.prompts.password("Enter the password details")
        else:
            self._prompt_iam_fields(source_creds)
        credential = {
            "secret_vault_type": secret_vault_type,
            "secret_vault_name": secret_vault_name,
            source: source_creds,
        }

        _save_to_disk(credential, cred_file)
        logger.info(f"Credential template created for {source}.")


class ConfigureSynapseAssessment(AssessmentConfigurator):
    """Synapse specific assessment configuration."""

    def _configure_credentials(self) -> None:
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


class ConfigureSnowflakeAssessment(AssessmentConfigurator):
    """Snowflake specific assessment configuration."""

    def _configure_credentials(self) -> None:
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


ConfiguratorFactory = Callable[[str, Prompts, str, Path | str | None], AssessmentConfigurator]


class ConfigureBigQueryAssessment(AssessmentConfigurator):
    @classmethod
    def _parse_project_region_pairs(cls, raw: str) -> list[dict[str, str]]:
        """Parse `project.region, project.region, ...` into a list of {project, region} dicts.

        Uses Google's fully-qualified resource-path convention
        (https://cloud.google.com/iam/docs/full-resource-names#bigquery). Each token must
        contain exactly one `.` with non-empty sides; empty tokens are ignored (so
        trailing/duplicate commas are tolerated). Raises ValueError on malformed input —
        the caller surfaces this to the user during interactive configuration.

        GCP project IDs cannot contain `.`, so splitting on the single dot is unambiguous.
        """
        pairs: list[dict[str, str]] = []
        for token in raw.split(","):
            token = token.strip()
            if not token:
                continue
            if token.count(".") != 1:
                raise ValueError(f"Invalid project/region pair '{token}': expected exactly one '.' (e.g. proj-a.us)")
            project, _, region = token.partition(".")
            project, region = project.strip(), region.strip()
            if not project or not region:
                raise ValueError(f"Invalid project/region pair '{token}': both sides of '.' must be non-empty")
            pairs.append({"project": project, "region": region})
        if not pairs:
            raise ValueError("At least one project/region pair is required (e.g. proj-a.us)")
        return pairs

    def _configure_credentials(self) -> None:
        cred_file = self._credential_file
        source = self._source_name

        logger.info(
            "\n(local | env) \nlocal means values are read as plain text \nenv means values are read "
            "from environment variables fall back to plain text if not variable is not found\n",
        )
        secret_vault_type = str(self.prompts.choice("Enter secret vault type (local | env)", ["local", "env"])).lower()
        secret_vault_name = None

        logger.info("Please provide BigQuery connection settings:")
        pairs_raw = self.prompts.question(
            "Enter BigQuery project and region pairs "
            "(Format: comma-separated project.region. Example: my-proj-a.us, my-proj-b.eu-west-1)"
        )
        pairs = self._parse_project_region_pairs(pairs_raw)

        profiling_window_days = int(
            self.prompts.question("Enter lookback window in days to profile", default="180", valid_number=True)
        )
        max_parallel_sqls = int(
            self.prompts.question(
                "Enter max parallel SQLs per (project, region) iteration", default="8", valid_number=True
            )
        )

        logger.info("Please configure profiler settings:")
        bigquery_profiler = {
            "profiling_window_days": profiling_window_days,
            "max_parallel_sqls": max_parallel_sqls,
            "exclude_reservations_data": self.prompts.confirm("Exclude reservations and commitments data?"),
            "exclude_streaming_metrics": self.prompts.confirm("Exclude streaming and write API summary?"),
        }

        credential = {
            "secret_vault_type": secret_vault_type,
            "secret_vault_name": secret_vault_name,
            source: {
                "pairs": pairs,
                "profiler": bigquery_profiler,
            },
        }
        _save_to_disk(credential, cred_file)

        logger.info(f"Credential template created for {source}.")


def create_assessment_configurator(
    source_system: str, product_name: str, prompts: Prompts, credential_file: Path | str | None = None
) -> AssessmentConfigurator:
    configurators: dict[str, ConfiguratorFactory] = {
        "mssql": ConfigureSqlServerAssessment,
        "redshift": ConfigureRedshiftAssessment,
        "synapse": ConfigureSynapseAssessment,
        "snowflake": ConfigureSnowflakeAssessment,
        "legacy_synapse": ConfigureSqlServerAssessment,
        "oracle": ConfigureOracleAssessment,
        "bigquery": ConfigureBigQueryAssessment,
    }

    key = source_system_family(source_system)
    if key not in configurators:
        raise ValueError(f"Unsupported source system: {source_system}")

    return configurators[key](product_name, prompts, key, credential_file)
