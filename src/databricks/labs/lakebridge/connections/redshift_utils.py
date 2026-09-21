"""
Redshift utility functions for endpoint (host) parsing.
"""

import re

# Standard AWS Redshift endpoints embed the region as the label immediately
# before ".redshift" / ".redshift-serverless", e.g.
#   <workgroup>.<account-id>.<region>.redshift-serverless.amazonaws.com  (Serverless)
#   <cluster>.<identifier>.<region>.redshift.amazonaws.com               (provisioned)
# Region labels look like us-east-1, eu-west-1, ap-southeast-2, us-gov-west-1.
# The ".amazonaws.com.cn" suffix (China partition) is matched only so it can be
# rejected explicitly below.
_REDSHIFT_ENDPOINT = re.compile(
    r"\.(?P<region>[a-z]{2}(?:-[a-z]+)+-\d+)\.redshift(?P<serverless>-serverless)?\.amazonaws\.com(?:\.cn)?$",
    re.IGNORECASE,
)


def parse_redshift_endpoint(host: str) -> tuple[str, bool] | None:
    """Extract ``(region, is_serverless)`` from a standard AWS Redshift endpoint host.

    Returns the region lower-cased (e.g. ``us-east-1``) together with whether the
    endpoint is Redshift Serverless (the ``-serverless`` label). Returns ``None``
    for a non-standard host (custom DNS/CNAME, PrivateLink alias, bare hostname)
    that the pattern cannot validate, so the caller can fall back.

    China (regions ``cn-north-1`` / ``cn-northwest-1``, hosts ending
    ``.amazonaws.com.cn``) is explicitly unsupported: it is a separate AWS
    partition whose Serverless pricing is not served by the global AWS Price List
    API this profiler queries, so a China host also returns ``None``.

    Args:
        host: Redshift endpoint host. A leading ``http(s)://`` scheme and a
            trailing ``:port`` or ``/path`` are stripped; the host is not
            otherwise URL-parsed.
    """
    if not host:
        return None
    cleaned = host.strip().replace("https://", "").replace("http://", "")
    cleaned = cleaned.split("/", 1)[0].split(":", 1)[0]
    match = _REDSHIFT_ENDPOINT.search(cleaned)
    if not match:
        return None
    region = match.group("region").lower()
    if region.startswith("cn-"):  # China partition: explicitly unsupported (see docstring).
        return None
    return region, bool(match.group("serverless"))
