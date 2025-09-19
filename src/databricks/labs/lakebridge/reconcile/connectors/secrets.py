import base64
import logging

from databricks.sdk import WorkspaceClient
from databricks.sdk.errors import NotFound

logger = logging.getLogger(__name__)


# TODO use CredentialManager to allow for changing secret provider for tests
class SecretsMixin:
    _ws: WorkspaceClient
    _secret_scope: str

    def _get_secret_or_none(self, secret_key: str) -> str | None:
        """
        Get the secret value given a secret scope & secret key. Log a warning if secret does not exist
        Used To ensure backwards compatibility when supporting new secrets
        """
        try:
            # Return the decoded secret value in string format
            return self._get_secret(secret_key)
        except NotFound as e:
            logger.warning(f"Secret not found: key={secret_key}")
            logger.debug("Secret lookup failed", exc_info=e)
            return None

    def _get_secret(self, secret_key: str) -> str:
        """Get the secret value given a secret scope & secret key.

        Raises:
          NotFound: The secret could not be found.
          UnicodeDecodeError: The secret value was not Base64-encoded UTF-8.
        """
        try:
            # Return the decoded secret value in string format
            secret = self._ws.secrets.get_secret(self._secret_scope, secret_key)
            assert secret.value is not None
            return base64.b64decode(secret.value).decode("utf-8")
        except NotFound as e:
            raise NotFound(f'Secret does not exist with scope: {self._secret_scope} and key: {secret_key} : {e}') from e
        except UnicodeDecodeError as e:
            raise UnicodeDecodeError(
                "utf-8",
                secret_key.encode(),
                0,
                1,
                f"Secret {self._secret_scope}/{secret_key} has Base64 bytes that cannot be decoded to utf-8 string: {e}.",
            ) from e
