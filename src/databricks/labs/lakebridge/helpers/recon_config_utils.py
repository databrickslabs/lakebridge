import logging

from databricks.labs.blueprint.tui import Prompts
from databricks.labs.lakebridge.reconcile.constants import ReconSourceType
from databricks.sdk import WorkspaceClient
from databricks.sdk.errors.platform import ResourceDoesNotExist

logger = logging.getLogger(__name__)


class ReconConfigPrompts:
    def __init__(self, ws: WorkspaceClient, prompts: Prompts = Prompts()):
        self._source = None
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

    def prompt_source(self):
        source = self._prompts.choice(
            "Select the source dialect", [source_type.value for source_type in ReconSourceType]
        )
        self._source = source
        return source

    def _prompt_snowflake_connection_details(self) -> tuple[str, dict[str, str]]:
        """
        Prompt for Snowflake connection details
        :return: tuple[str, dict[str, str]]
        """
        logger.info(
            f"Please answer a couple of questions to configure `{ReconSourceType.SNOWFLAKE.value}` Connection profile"
        )

        sf_url = self._prompts.question("Enter Snowflake URL")
        account = self._prompts.question("Enter Account Name")
        sf_user = self._prompts.question("Enter User")
        sf_password = self._prompts.question("Enter Password")
        sf_db = self._prompts.question("Enter Database")
        sf_schema = self._prompts.question("Enter Schema")
        sf_warehouse = self._prompts.question("Enter Snowflake Warehouse")
        sf_role = self._prompts.question("Enter Role", default=" ")

        sf_conn_details = {
            "sfUrl": sf_url,
            "account": account,
            "sfUser": sf_user,
            "sfPassword": sf_password,
            "sfDatabase": sf_db,
            "sfSchema": sf_schema,
            "sfWarehouse": sf_warehouse,
            "sfRole": sf_role,
        }

        sf_conn_dict = (ReconSourceType.SNOWFLAKE.value, sf_conn_details)
        return sf_conn_dict

    def _prompt_oracle_connection_details(self) -> tuple[str, dict[str, str]]:
        """
        Prompt for Oracle connection details
        :return: tuple[str, dict[str, str]]
        """
        logger.info(
            f"Please answer a couple of questions to configure `{ReconSourceType.ORACLE.value}` Connection profile"
        )
        user = self._prompts.question("Enter User")
        password = self._prompts.question("Enter Password")
        host = self._prompts.question("Enter host")
        port = self._prompts.question("Enter port")
        database = self._prompts.question("Enter database/SID")

        oracle_conn_details = {"user": user, "password": password, "host": host, "port": port, "database": database}

        oracle_conn_dict = (ReconSourceType.ORACLE.value, oracle_conn_details)
        return oracle_conn_dict

    def _connection_details(self):
        """
        Prompt for connection details based on the source
        :return: None
        """
        logger.debug(f"Prompting for `{self._source}` connection details")
        match self._source:
            case ReconSourceType.SNOWFLAKE.value:
                return self._prompt_snowflake_connection_details()
            case ReconSourceType.ORACLE.value:
                return self._prompt_oracle_connection_details()

    def prompt_and_save_connection_details(self):
        """
        Prompt for connection details and save them as Secrets in Databricks Workspace
        """
        # prompt for connection_details only if source is other than Databricks
        if self._source == ReconSourceType.DATABRICKS.value:
            logger.info("*Databricks* as a source is supported only for **Hive MetaStore (HMS) setup**")
            return

        # Prompt for secret scope
        scope_name = self._prompts.question("Enter Secret Scope name")
        self._ensure_scope_exists(scope_name)

        # Prompt for connection details
        connection_details = self._connection_details()
        logger.debug(f"Storing `{self._source}` connection details as Secrets in Databricks Workspace...")
        self.store_connection_secrets(scope_name, connection_details)
