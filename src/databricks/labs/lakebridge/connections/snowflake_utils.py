"""
Snowflake utility functions for account URL parsing and connection handling.
"""

import re


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


def validate_snowflake_account(account_identifier: str) -> bool:
    """
    Basic validation of Snowflake account identifier format.

    Args:
        account_identifier: Clean account identifier

    Returns:
        True if format appears valid, False otherwise
    """
    if not account_identifier:
        return False

    # Snowflake account identifiers typically contain letters, numbers, and hyphens
    # They cannot be empty and should not contain spaces or special characters
    pattern = r'^[A-Za-z0-9\-_]+$'
    return bool(re.match(pattern, account_identifier))
