"""Tests for the extraction-side ClickHouseConnection secure/port defaults.

This is the connection that carries the password during profiling. Its secure/port normalization
must match the probe ClickHouseConnector (both go through normalize_secure_and_port), so a
hand-written credentials file can never open a plaintext connection to a Cloud host — the gap that
originally slipped because only the probe connector was covered.
"""

from unittest.mock import MagicMock, patch

import pytest

from databricks.labs.lakebridge.resources.assessments.clickhouse import (
    normalize_secure_and_port,
    parse_bool,
)
from databricks.labs.lakebridge.resources.assessments.clickhouse.connection import ClickHouseConnection


@pytest.mark.parametrize(
    ("config", "expected_secure", "expected_port"),
    [
        # Cloud host: forced TLS/8443 regardless of a missing or falsey `secure`.
        ({"host": "svc.us-east-1.aws.clickhouse.cloud"}, True, 8443),
        ({"host": "svc.us-east-1.aws.clickhouse.cloud", "secure": "false"}, True, 8443),
        ({"host": "svc.us-east-1.aws.clickhouse.cloud", "secure": False}, True, 8443),
        # OSS / self-managed: plaintext/8123 by default.
        ({"host": "127.0.0.1"}, False, 8123),
        ({"host": "10.0.0.5", "secure": "true"}, True, 8443),
        ({"host": "10.0.0.5", "secure": True}, True, 8443),
        # Explicit port always wins over the derived default.
        ({"host": "127.0.0.1", "port": "9000"}, False, 9000),
        ({"host": "svc.aws.clickhouse.cloud", "port": 8443, "secure": "false"}, True, 8443),
    ],
)
def test_normalize_secure_and_port(config, expected_secure, expected_port) -> None:
    assert normalize_secure_and_port(config) == (expected_secure, expected_port)


def test_parse_bool_does_not_coerce_string_false() -> None:
    """bool("false") is True in Python; parse_bool must read the content instead."""
    assert parse_bool("false") is False
    assert parse_bool("no") is False
    assert parse_bool("0") is False
    assert parse_bool("true") is True
    assert parse_bool("YES") is True
    assert parse_bool(True) is True
    assert parse_bool(None, default=True) is True
    assert parse_bool("garbage") is False


@patch("databricks.labs.lakebridge.resources.assessments.clickhouse.connection.clickhouse_connect")
def test_extraction_connection_cloud_host_cannot_be_downgraded(mock_clickhouse_connect) -> None:
    """The extraction connection (carries the password) must force TLS/8443 for a Cloud host even
    when the creds file says secure="false" or secure=False."""
    mock_clickhouse_connect.get_client.return_value = MagicMock()

    for bad_secure in ("false", False, "no", 0):
        mock_clickhouse_connect.get_client.reset_mock()
        conn = ClickHouseConnection(
            {"host": "abc.us-east-1.aws.clickhouse.cloud", "password": "p", "secure": bad_secure}
        )
        conn.connect()
        kwargs = mock_clickhouse_connect.get_client.call_args.kwargs
        assert kwargs["secure"] is True, f"cloud host downgraded with secure={bad_secure!r}"
        assert kwargs["port"] == 8443


@patch("databricks.labs.lakebridge.resources.assessments.clickhouse.connection.clickhouse_connect")
def test_extraction_connection_oss_defaults_plaintext(mock_clickhouse_connect) -> None:
    """A self-managed host with no `secure` connects plaintext on 8123."""
    mock_clickhouse_connect.get_client.return_value = MagicMock()
    conn = ClickHouseConnection({"host": "127.0.0.1", "password": "p"})
    conn.connect()
    kwargs = mock_clickhouse_connect.get_client.call_args.kwargs
    assert kwargs["secure"] is False
    assert kwargs["port"] == 8123
