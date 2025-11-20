import logging

from databricks.labs.blueprint.tui import Prompts
from databricks.labs.lakebridge.reconcile.constants import ReconSourceType
from databricks.sdk import WorkspaceClient
from databricks.sdk.errors.platform import ResourceDoesNotExist

logger = logging.getLogger(__name__)


class ReconConfigPrompts:
    def __init__(self, ws: WorkspaceClient, prompts: Prompts = Prompts()):
        self._prompts = prompts
        self._ws = ws

    def _scope_exists(self, scope_name: str) -> bool:
        scope_exists = scope_name in [scope.name for scope in self._ws.secrets.list_scopes()]

        if not scope_exists:
            logger.error(
                f"Error: Cannot find Secret Scope: `{scope_name}` in Databricks Workspace."
                f"\nUse `remorph configure-secrets` to setup Scope and Secrets"
            )
            return False
        logger.debug(f"Found Scope: `{scope_name}` in Databricks Workspace")
        return True

    def _ensure_scope_exists(self, scope_name: str):
        """
        Get or Create a new Scope in Databricks Workspace
        :param scope_name:
        """
        scope_exists = self._scope_exists(scope_name)
        if not scope_exists:
            allow_scope_creation = self._prompts.confirm("Do you want to create a new one?")
            if not allow_scope_creation:
                msg = "Scope is needed to store Secrets in Databricks Workspace"
                raise SystemExit(msg)

            try:
                logger.debug(f" Creating a new Scope: `{scope_name}`")
                self._ws.secrets.create_scope(scope_name)
            except Exception as ex:
                logger.error(f"Exception while creating Scope `{scope_name}`: {ex}")
                raise ex

            logger.info(f" Created a new Scope: `{scope_name}`")
        logger.info(f" Using Scope: `{scope_name}`...")

    def _secret_key_exists(self, scope_name: str, secret_key: str) -> bool:
        try:
            self._ws.secrets.get_secret(scope_name, secret_key)
            logger.info(f"Found Secret key `{secret_key}` in Scope `{scope_name}`")
            return True
        except ResourceDoesNotExist:
            logger.debug(f"Secret key `{secret_key}` not found in Scope `{scope_name}`")
            return False

    def _store_secret(self, scope_name: str, secret_key: str, secret_value: str):
        try:
            logger.debug(f"Storing Secret: *{secret_key}* in Scope: `{scope_name}`")
            self._ws.secrets.put_secret(scope=scope_name, key=secret_key, string_value=secret_value)
        except Exception as ex:
            logger.error(f"Exception while storing Secret `{secret_key}`: {ex}")
            raise ex

    def store_connection_secrets(self, scope_name: str, conn_details: tuple[str, dict[str, str]]):
        engine = conn_details[0]
        secrets = conn_details[1]

        logger.debug(f"Storing `{engine}` Connection Secrets in Scope: `{scope_name}`")

        for key, value in secrets.items():
            secret_key = key
            logger.debug(f"Processing Secret: *{secret_key}*")
            debug_op = "Storing"
            info_op = "Stored"
            if self._secret_key_exists(scope_name, secret_key):
                overwrite_secret = self._prompts.confirm(f"Do you want to overwrite `{secret_key}`?")
                if not overwrite_secret:
                    continue
                debug_op = "Overwriting"
                info_op = "Overwritten"

            logger.debug(f"{debug_op} Secret: *{secret_key}* in Scope: `{scope_name}`")
            self._store_secret(scope_name, secret_key, value)
            logger.info(f"{info_op} Secret: *{secret_key}* in Scope: `{scope_name}`")

    def _prompt_snowflake_connection_details(self) -> dict[str, str]:
        """
        Prompt for Snowflake connection details
        :return: tuple[str, dict[str, str]]
        """
        logger.info(
            f"Please answer a couple of questions to configure `{ReconSourceType.SNOWFLAKE.value}` Connection profile"
        )

        sf_url = self._prompts.question("Enter Snowflake URL Secret")
        sf_user = self._prompts.question("Enter User Secret")
        password_dict = {}
        sf_password = self._prompts.question("Enter Password Secret if using password authentication else leave blank")
        if not sf_password:
            logger.info("Proceeding with PEM Private Key authentication...")
            sf_pem_key = self._prompts.question("Enter PEM Private Key Secret")
            password_dict["pem_private_key"] = sf_pem_key
            sf_pem_key_password = self._prompts.question(
                "Enter PEM Private Key Password Secret if used else leave blank"
            )
            if sf_pem_key_password:
                password_dict["pem_private_key_password"] = sf_pem_key_password
        else:
            password_dict["sfPassword"] = sf_password
        sf_db = self._prompts.question("Enter Database Secret")
        sf_schema = self._prompts.question("Enter Schema Secret")
        sf_warehouse = self._prompts.question("Enter Snowflake Warehouse Secret")
        sf_role = self._prompts.question("Enter Role Secret")

        sf_conn_details = {
            "sfUrl": sf_url,
            "sfUser": sf_user,
            "sfDatabase": sf_db,
            "sfSchema": sf_schema,
            "sfWarehouse": sf_warehouse,
            "sfRole": sf_role,
        } | password_dict

        return sf_conn_details

    def _prompt_oracle_connection_details(self) -> dict[str, str]:
        """
        Prompt for Oracle connection details
        :return: tuple[str, dict[str, str]]
        """
        logger.info(
            f"Please answer a couple of questions to configure `{ReconSourceType.ORACLE.value}` Connection profile"
        )
        user = self._prompts.question("Enter User Secret")
        password = self._prompts.question("Enter Password Secret")
        host = self._prompts.question("Enter host Secret")
        port = self._prompts.question("Enter port Secret")
        database = self._prompts.question("Enter database/SID Secret")

        oracle_conn_details = {"user": user, "password": password, "host": host, "port": port, "database": database}

        return oracle_conn_details

    def _prompt_mssql_connection_details(self) -> dict[str, str]:
        """
        Prompt for Oracle connection details
        :return: tuple[str, dict[str, str]]
        """
        logger.info(
            f"Please answer a couple of questions to configure `{ReconSourceType.MSSQL.value}`/`{ReconSourceType.SYNAPSE.value}` Connection profile"
        )
        user = self._prompts.question("Enter User Secret")
        password = self._prompts.question("Enter Password Secret")
        host = self._prompts.question("Enter host Secret")
        port = self._prompts.question("Enter port Secret")
        database = self._prompts.question("Enter database Secret")
        encrypt = self._prompts.question("Enter Encrypt Secret")
        trust_server_certificate = self._prompts.question("Enter Trust Server Certificate Secret")

        tsql_conn_details = {
            "user": user,
            "password": password,
            "host": host,
            "port": port,
            "database": database,
            "encrypt": encrypt,
            "trustServerCertificate": trust_server_certificate,
        }

        return tsql_conn_details

    def _connection_details(self, source: str):
        logger.debug(f"Prompting for `{source}` connection details")
        match source:
            case ReconSourceType.SNOWFLAKE.value:
                return self._prompt_snowflake_connection_details()
            case ReconSourceType.ORACLE.value:
                return self._prompt_oracle_connection_details()
            case ReconSourceType.MSSQL.value | ReconSourceType.SYNAPSE.value:
                return self._prompt_mssql_connection_details()

    def prompt_recon_creds(self, source: str):
        logger.info(
            "\n(local | env | databricks) \nlocal means values are read as plain text \nenv means values are read "
            "from environment variables fall back to plain text if not variable is not found\ndatabricks means values are read from Databricks Secrets\n",
        )
        secret_vault_type = str(
            self._prompts.choice("Enter secret vault type (local | env | databricks)", ["local", "env", "databricks"])
        ).lower()

        if secret_vault_type == "databricks":
            logger.info(
                "Since you have chosen `databricks` as secret vault type, you need to provide secret names in the following steps in the format <secret_scope>/<secret_key>"
            )

        connection_details = self._connection_details(source)
        return secret_vault_type, connection_details
