"""Apache Ranger Admin helpers shared by the ``ranger_*`` steps (see endpoints.py).

Ranger 2.x serves two policy APIs. The original profiler reads the legacy v1 API (``/service/public/api/repository``
and ``/service/public/api/policy``). On Ranger 2.9.0 the v1 policy list returns 20 of 24 policies although its
``totalCount`` says 24, and the v1 repository list leaves out the YARN and tag services. The v1 calls therefore
under-report, so this profiler reads the v2 API. It uses v1 only when v2 answers 404 (very old Ranger releases), and
logs a warning that the result may be incomplete.

The v2 list endpoints return a top-level JSON array. The v1 endpoints wrap their list in ``vXRepositories`` or
``vXPolicies`` with a ``totalCount``. The v1 bodies are translated to the v2 shape here, so the step SQL reads one
shape. v1 has no deny, exception, masking or row-filter items, so a translated v1 policy has only its allow items.

The ``xusers`` endpoints (users and groups) are admin-only and page with ``pageSize`` and ``startIndex``.
"""

import json
import logging
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from databricks.labs.lakebridge.resources.assessments.hadoop.rest_client import HadoopHttpError, RestClient

logger = logging.getLogger(__name__)

V2_SERVICE_PATH = "/service/public/v2/api/service"
V2_POLICY_PATH = "/service/public/v2/api/policy"
V2_SERVICE_DEF_PATH = "/service/public/v2/api/servicedef"
V1_REPOSITORY_PATH = "/service/public/api/repository"
V1_POLICY_PATH = "/service/public/api/policy"
XUSERS_USERS_PATH = "/service/xusers/users"
XUSERS_GROUPS_PATH = "/service/xusers/groups"

PAGE_SIZE = 500
_NOT_FOUND = 404
_V1_WARNING = (
    "Ranger's v2 %s API is not available (HTTP 404), so the profiler reads the legacy v1 API. "
    "On Ranger 2.x the v1 API under-reports YARN and tag services and policies; the result may be incomplete."
)

# A page fetcher: given a start index, returns the rows on that page and the total the server reports (or None).
PageFetch = Callable[[int], tuple[list[Any], int | None]]


def list_services(client: RestClient) -> list[dict[str, Any]]:
    """Every Ranger service (repository), in the v2 shape. Falls back to v1 when v2 is not there."""
    try:
        return _page_through(_list_page(client, V2_SERVICE_PATH))
    except HadoopHttpError as e:
        if e.status_code != _NOT_FOUND:
            raise
        logger.warning(_V1_WARNING % "service")
        repositories = _page_through(_wrapped_page(client, V1_REPOSITORY_PATH, "vXRepositories"))
        return [_repository_as_service(repository) for repository in repositories]


def list_service_defs(client: RestClient) -> str:
    """The service-definition list as Ranger returns it. There is no v1 equivalent, so a 404 is an error."""
    return client.get_text(V2_SERVICE_DEF_PATH)


def list_policies(client: RestClient) -> list[dict[str, Any]]:
    """Every policy of every service, in the v2 shape. Falls back to v1 (translated) when v2 is not there."""
    try:
        return _page_through(_list_page(client, V2_POLICY_PATH))
    except HadoopHttpError as e:
        if e.status_code != _NOT_FOUND:
            raise
    logger.warning(_V1_WARNING % "policy")
    return [_policy_as_v2(policy) for policy in _page_through(_wrapped_page(client, V1_POLICY_PATH, "vXPolicies"))]


def list_users(client: RestClient) -> list[dict[str, Any]]:
    return _page_through(_wrapped_page(client, XUSERS_USERS_PATH, "vXUsers"))


def list_groups(client: RestClient) -> list[dict[str, Any]]:
    return _page_through(_wrapped_page(client, XUSERS_GROUPS_PATH, "vXGroups"))


def _list_page(client: RestClient, path: str) -> PageFetch:
    """One page of a v2 endpoint, which answers with a top-level array."""

    def fetch(start: int) -> tuple[list[Any], int | None]:
        body = client.get_json(path, {"pageSize": PAGE_SIZE, "startIndex": start})
        return (body if isinstance(body, list) else []), None

    return fetch


def _wrapped_page(client: RestClient, path: str, key: str) -> PageFetch:
    """One page of an endpoint that wraps its rows in ``key`` and reports ``totalCount``."""

    def fetch(start: int) -> tuple[list[Any], int | None]:
        body = client.get_json(path, {"pageSize": PAGE_SIZE, "startIndex": start})
        if not isinstance(body, dict):
            return [], None
        rows = body.get(key)
        total = body.get("totalCount")
        return (rows if isinstance(rows, list) else []), (total if isinstance(total, int) else None)

    return fetch


def _page_through(fetch_page: PageFetch) -> list[dict[str, Any]]:
    """Read pages until a page adds no new rows or the total is reached. Rows are deduplicated by ``id``.

    The next start index is the number of rows the server returned, so a server that caps the page size below
    ``PAGE_SIZE`` is still read to the end. A server that ignores ``startIndex`` repeats its rows, which is caught
    as "no new rows" rather than looping.
    """
    collected: list[dict[str, Any]] = []
    seen: set[Any] = set()
    start = 0
    while True:
        rows, total = fetch_page(start)
        added = 0
        for row in rows:
            if not isinstance(row, dict):
                continue
            row_id = row.get("id")
            if row_id is not None:
                if row_id in seen:
                    continue
                seen.add(row_id)
            collected.append(row)
            added += 1
        start += len(rows)
        if not rows or not added:
            break
        if total is not None and start >= total:
            break
    return collected


def _policy_as_v2(policy: dict[str, Any]) -> dict[str, Any]:
    """A v1 policy as the v2 policy shape the policy steps read.

    v1 lists resources by kind (databases, tables, columns, udfs) or by one resource name, and grants access with
    ``permMapList``, where ``admin`` means delegate admin.
    """
    resources: dict[str, Any] = {}
    if policy.get("resourceName"):
        resources["resourceName"] = {"values": [policy["resourceName"]]}
    for kind, key in (("database", "databases"), ("table", "tables"), ("column", "columns"), ("udf", "udfs")):
        values = policy.get(key)
        if values:
            resources[kind] = {"values": list(values)}

    items = []
    for grant in policy.get("permMapList") or []:
        perms = grant.get("permList") or []
        items.append(
            {
                "users": list(grant.get("userList") or []),
                "groups": list(grant.get("groupList") or []),
                "accesses": [{"type": perm, "isAllowed": True} for perm in perms if perm != "admin"],
                "delegateAdmin": "admin" in perms,
            }
        )
    return {
        "id": policy.get("id"),
        "guid": None,
        "isEnabled": policy.get("isEnabled"),
        "version": _int_or_none(policy.get("version")),
        "service": policy.get("repositoryName"),
        "serviceType": policy.get("repositoryType"),
        "name": policy.get("policyName"),
        "policyType": _int_or_none(policy.get("policyType")) or 0,
        "description": policy.get("description"),
        "isAuditEnabled": policy.get("isAuditEnabled"),
        "resources": resources,
        "policyItems": items,
    }


def _repository_as_service(repository: dict[str, Any]) -> dict[str, Any]:
    """A v1 repository as the v2 service shape."""
    return {
        "id": repository.get("id"),
        "name": repository.get("name"),
        "type": repository.get("repositoryType"),
        "displayName": repository.get("name"),
        "description": repository.get("description"),
        "isEnabled": repository.get("isActive"),
        "version": _int_or_none(repository.get("version")),
        "createTime": _epoch_ms(repository.get("createDate")),
        "updateTime": _epoch_ms(repository.get("updateDate")),
        "createdBy": repository.get("owner"),
        "updatedBy": repository.get("updatedBy"),
        "configs": _json_object(repository.get("config")),
    }


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _epoch_ms(text: Any) -> int | None:
    """An ISO-8601 UTC timestamp (as v1 reports it) as epoch milliseconds, the v2 form."""
    if not isinstance(text, str) or not text:
        return None
    try:
        moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return int(moment.timestamp() * 1000)


def _json_object(text: Any) -> dict[str, Any]:
    """v1 sends a repository's configs as a JSON string inside the JSON body."""
    if isinstance(text, dict):
        return text
    if not isinstance(text, str) or not text:
        return {}
    try:
        value = json.loads(text)
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}
