import logging

from databricks.labs.blueprint.tui import Prompts
from databricks.labs.lakebridge.reconcile.constants import ReconSourceType
from databricks.sdk import WorkspaceClient

logger = logging.getLogger(__name__)


class ReconConfigPrompts:
    def __init__(self, ws: WorkspaceClient, prompts: Prompts = Prompts()):
        self._prompts = prompts
        self._ws = ws

    def _prompt_snowflake_connection_details(self) -> dict[str, str]:
        """
        Prompt for Snowflake connection details
        :return: tuple[str, dict[str, str]]
        """
        logger.info(
            f"Please answer a couple of questions to configure `{ReconSourceType.SNOWFLAKE.value}` Connection profile"
        )

        sf_url = self._prompts.question("Enter Snowflake URL Secret Name")
        sf_user = self._prompts.question("Enter User Secret Name")
        password_dict = {}
        sf_password = self._prompts.question(
            "Enter Password Secret Name or use `None` to use key-based auth", default="None"
        )
        if sf_password.lower() == "none":
            logger.info("Proceeding with PEM Private Key authentication...")
            sf_pem_key = self._prompts.question("Enter PEM Private Key Secret Name")
            password_dict["pem_private_key"] = sf_pem_key
            sf_pem_key_password = self._prompts.question(
                "Enter PEM Private Key Password Secret Name or use `None`", default="None"
            )
            if sf_pem_key_password.lower() == "none":
                password_dict["pem_private_key_password"] = sf_pem_key_password
        else:
            password_dict["sfPassword"] = sf_password
        sf_db = self._prompts.question("Enter Database Secret Name")
        sf_schema = self._prompts.question("Enter Schema Secret Name")
        sf_warehouse = self._prompts.question("Enter Snowflake Warehouse Secret Name")
        sf_role = self._prompts.question("Enter Role Secret Name")

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
        user = self._prompts.question("Enter User Secret Name")
        password = self._prompts.question("Enter Password Secret Name")
        host = self._prompts.question("Enter host Secret Name")
        port = self._prompts.question("Enter port Secret Name")
        database = self._prompts.question("Enter database/SID Secret Name")

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
        user = self._prompts.question("Enter User Secret Name")
        password = self._prompts.question("Enter Password Secret Name")
        host = self._prompts.question("Enter host Secret Name")
        port = self._prompts.question("Enter port Secret Name")
        database = self._prompts.question("Enter database Secret Name")
        encrypt = self._prompts.question("Enter Encrypt Secret Name")
        trust_server_certificate = self._prompts.question("Enter Trust Server Certificate Secret Name")

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

    def prompt_recon_creds(self, source: str) -> tuple[str, dict[str, str]]:
        logger.info("Please provide secret names in the following steps in the format <secret_scope>/<secret_key>")
        connection_details = self._connection_details(source)
        return "databricks", connection_details
