"""
Snowflake utility functions for account URL parsing and connection handling.
"""

import re
from pathlib import Path

from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives import serialization

# Account identifiers are org-account (MYORG-MYACCOUNT) or a legacy locator,
# which may carry region/cloud segments separated by dots (xy12345.us-east-1.aws).
# Allow letters, digits, hyphen, underscore and dot; anything else (notably a
# space) would break the connection URL, so reject it early with a clear error.
_ACCOUNT_IDENTIFIER = re.compile(r"^[A-Za-z0-9._-]+$")


def is_valid_snowflake_account(account_identifier: str) -> bool:
    """Return True if the identifier has no characters that would break the URL."""
    return bool(account_identifier) and bool(_ACCOUNT_IDENTIFIER.match(account_identifier))


def load_snowflake_private_key(key_path: Path, passphrase: str | None = None) -> bytes:
    """Load a PEM PKCS8 private key and return DER bytes for SQLAlchemy connect_args.

    Snowflake SQLAlchemy expects the private key as unencrypted DER PKCS8 bytes via
    ``connect_args={"private_key": ...}``. Encrypted ``.p8`` files need ``passphrase``.
    """
    try:
        key_bytes = key_path.read_bytes()
    except OSError as e:
        raise ConnectionError(f"Unable to read Snowflake private key at {key_path}: {e}") from e

    password = passphrase.encode() if passphrase else None
    try:
        private_key = serialization.load_pem_private_key(key_bytes, password=password)
    except (UnsupportedAlgorithm, TypeError, ValueError) as e:
        raise ConnectionError(
            f"Unable to load private key at {key_path}: {e} (check the key file and passphrase)"
        ) from e

    return private_key.private_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )


def parse_snowflake_account(account_input: str) -> str:
    """
    Parse Snowflake account identifier from various input formats.

    Users typically copy one of these formats from Snowflake console:
    - MYORG-MYACCOUNT.snowflakecomputing.com
    - https://MYORG-MYACCOUNT.snowflakecomputing.com
    - MYORG-MYACCOUNT (just the identifier)

    Returns the clean account identifier needed for connections.

    Args:
        account_input: Raw account URL or identifier from user

    Returns:
        Clean account identifier (e.g., MYORG-MYACCOUNT)
    """
    if not account_input:
        return ""

    # Remove https:// prefix if present
    account = account_input.replace("https://", "")

    # Remove .snowflakecomputing.com suffix if present
    account = account.replace(".snowflakecomputing.com", "")

    # Remove any path components (everything after first /)
    if "/" in account:
        account = account.split("/")[0]

    # Remove trailing slashes
    account = account.rstrip("/")

    return account
