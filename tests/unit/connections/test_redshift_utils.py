"""Unit tests for the Redshift endpoint-host parser."""

import pytest

from databricks.labs.lakebridge.connections.redshift_utils import parse_redshift_endpoint


@pytest.mark.parametrize(
    "host, expected",
    [
        # Serverless: <workgroup>.<account>.<region>.redshift-serverless.amazonaws.com
        ("my-wg.123456789012.us-east-1.redshift-serverless.amazonaws.com", ("us-east-1", True)),
        # Provisioned: <cluster>.<id>.<region>.redshift.amazonaws.com
        ("my-cluster.abc123.eu-west-1.redshift.amazonaws.com", ("eu-west-1", False)),
        # Multi-label region (GovCloud), serverless
        ("wg.123.us-gov-west-1.redshift-serverless.amazonaws.com", ("us-gov-west-1", True)),
        # host:port (provisioned)
        ("my-cluster.abc.us-west-2.redshift.amazonaws.com:5439", ("us-west-2", False)),
        # scheme + trailing slash (serverless)
        ("https://wg.acct.ap-southeast-2.redshift-serverless.amazonaws.com/", ("ap-southeast-2", True)),
        # Uppercase host -> region normalized to lowercase, serverless flag preserved
        ("WG.ACCT.US-EAST-1.REDSHIFT-SERVERLESS.AMAZONAWS.COM", ("us-east-1", True)),
        # China partition (.amazonaws.com.cn) is explicitly unsupported -> None
        ("cl.x.cn-north-1.redshift.amazonaws.com.cn", None),
        ("wg.acct.cn-northwest-1.redshift-serverless.amazonaws.com.cn", None),
        # Non-standard / custom DNS / PrivateLink alias -> None (caller falls back)
        ("redshift.mycorp.internal", None),
        ("localhost", None),
        ("", None),
    ],
)
def test_parse_redshift_endpoint(host, expected):
    assert parse_redshift_endpoint(host) == expected
