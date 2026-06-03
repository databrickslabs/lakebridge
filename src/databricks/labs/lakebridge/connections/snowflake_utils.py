"""
Snowflake utility functions for account URL parsing and connection handling.
"""

import re

# Account identifiers are org-account (MYORG-MYACCOUNT) or a legacy locator,
# which may carry region/cloud segments separated by dots (xy12345.us-east-1.aws).
# Allow letters, digits, hyphen, underscore and dot; anything else (notably a
# space) would break the connection URL, so reject it early with a clear error.
_ACCOUNT_IDENTIFIER = re.compile(r"^[A-Za-z0-9._-]+$")


def is_valid_snowflake_account(account_identifier: str) -> bool:
    """Return True if the identifier has no characters that would break the URL."""
    return bool(account_identifier) and bool(_ACCOUNT_IDENTIFIER.match(account_identifier))


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
