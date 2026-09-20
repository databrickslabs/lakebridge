"""Unit tests for the Redshift endpoint-host region parser."""

import pytest

from databricks.labs.lakebridge.connections.redshift_utils import parse_redshift_region


@pytest.mark.parametrize(
    "host, expected",
    [
        # Serverless: <workgroup>.<account>.<region>.redshift-serverless.amazonaws.com
        ("my-wg.123456789012.us-east-1.redshift-serverless.amazonaws.com", "us-east-1"),
        # Provisioned: <cluster>.<id>.<region>.redshift.amazonaws.com
        ("my-cluster.abc123.eu-west-1.redshift.amazonaws.com", "eu-west-1"),
        # Multi-label region (GovCloud)
        ("wg.123.us-gov-west-1.redshift-serverless.amazonaws.com", "us-gov-west-1"),
        # China partition (.amazonaws.com.cn)
        ("cl.x.cn-north-1.redshift.amazonaws.com.cn", "cn-north-1"),
        # host:port
        ("my-cluster.abc.us-west-2.redshift.amazonaws.com:5439", "us-west-2"),
        # scheme + trailing slash
        ("https://wg.acct.ap-southeast-2.redshift-serverless.amazonaws.com/", "ap-southeast-2"),
        # Uppercase host -> region normalized to lowercase
        ("WG.ACCT.US-EAST-1.REDSHIFT-SERVERLESS.AMAZONAWS.COM", "us-east-1"),
        # Non-standard / custom DNS / PrivateLink alias -> None (caller falls back)
        ("redshift.mycorp.internal", None),
        ("localhost", None),
        ("", None),
    ],
)
def test_parse_redshift_region(host, expected):
    assert parse_redshift_region(host) == expected
