"""
Redshift utility functions for endpoint (host) parsing.
"""

import re

# Redshift endpoints embed the AWS region as the label immediately before
# ".redshift" / ".redshift-serverless", e.g.
#   <workgroup>.<account-id>.<region>.redshift-serverless.amazonaws.com   (Serverless)
#   <cluster>.<identifier>.<region>.redshift.amazonaws.com                (provisioned)
# Region labels look like us-east-1, eu-west-1, ap-southeast-2, us-gov-west-1;
# China endpoints end in ".amazonaws.com.cn". This is AWS's standard endpoint
# format; custom DNS / PrivateLink aliases won't match (handled by the caller).
_REDSHIFT_ENDPOINT_REGION = re.compile(
    r"\.([a-z]{2}(?:-[a-z]+)+-\d+)\.redshift(?:-serverless)?\.amazonaws\.com(?:\.cn)?$",
    re.IGNORECASE,
)


def parse_redshift_region(host: str) -> str | None:
    """Extract the AWS region from a standard Redshift endpoint host.

    Returns the region lower-cased (e.g. ``us-east-1``) for a standard AWS
    Redshift or Redshift Serverless endpoint. Returns ``None`` for a
    non-standard host (custom DNS/CNAME, PrivateLink alias, bare hostname)
    that the pattern cannot validate, so the caller can fall back.

    Args:
        host: Redshift endpoint host, optionally with scheme/port/path noise.
    """
    if not host:
        return None
    cleaned = host.strip().replace("https://", "").replace("http://", "")
    cleaned = cleaned.split("/", 1)[0].split(":", 1)[0]
    match = _REDSHIFT_ENDPOINT_REGION.search(cleaned)
    return match.group(1).lower() if match else None
