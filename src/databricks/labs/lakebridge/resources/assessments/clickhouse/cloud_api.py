"""ClickHouse Cloud API client.

Pulls authoritative service metadata — provider, region, and the real compute
sizing (replica memory, replica count) — so cost estimates are grounded in the
actual service configuration instead of being guessed from the hostname and
hardcoded defaults.

Read-only. Uses HTTP Basic auth (Cloud API Key ID / Key Secret) against
https://api.clickhouse.cloud/v1. Depends only on the Python standard library
(urllib), so it adds no third-party requirements.

Note on tier: per the ClickHouse Cloud API docs, the `tier` field was removed
from the service object ("we no longer have service tiers" under the new
pricing plans). Plan (Basic / Scale / Enterprise) is an organization-level
billing concept and is not exposed per service, so `normalize_service` reports
tier as None. Provider and region are exposed and authoritative.
"""

import base64
import json
import urllib.error
import urllib.request
from typing import Any, Optional

API_BASE = "https://api.clickhouse.cloud/v1"


class CloudAPIError(Exception):
    """Raised when the Cloud API is unreachable or returns an error."""


def normalize_service(svc: dict, org_id: Optional[str] = None) -> dict[str, Any]:
    """Normalize a raw Cloud API service object into the fields the profiler uses."""
    provider = (svc.get("provider") or "").lower()
    region = (svc.get("region") or "").lower()
    region_key = f"{provider}:{region}" if provider and region else "default"

    https_host = None
    for ep in svc.get("endpoints", []) or []:
        if ep.get("protocol") == "https":
            https_host = ep.get("host")
            break

    return {
        "service_id": svc.get("id"),
        "organization_id": org_id,
        "name": svc.get("name"),
        "provider": provider,
        "region": region,
        "region_key": region_key,
        "state": svc.get("state"),
        "profile": svc.get("profile"),
        "min_replica_memory_gb": svc.get("minReplicaMemoryGb"),
        "num_replicas": svc.get("numReplicas"),
        "https_host": https_host,
        # Tier was removed from the service object in the new pricing model
        # (see module docstring) — callers must supply it explicitly if needed.
        "tier": None,
    }


class ClickHouseCloudAPI:
    """Minimal read-only client for the ClickHouse Cloud API."""

    def __init__(self, key_id: str, key_secret: str, base_url: str = API_BASE, timeout: int = 15):
        self.key_id = key_id
        self.key_secret = key_secret
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _get(self, path: str) -> Any:
        url = f"{self.base_url}{path}"
        token = base64.b64encode(f"{self.key_id}:{self.key_secret}".encode()).decode()
        req = urllib.request.Request(url, headers={"Authorization": f"Basic {token}"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                payload = json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            raise CloudAPIError(f"GET {path} -> HTTP {e.code}") from e
        except urllib.error.URLError as e:
            raise CloudAPIError(f"GET {path} -> {e.reason}") from e
        except (ValueError, json.JSONDecodeError) as e:
            # Non-JSON body (e.g. a proxy's HTML error page): surface as CloudAPIError, not a bare ValueError.
            raise CloudAPIError(f"GET {path} -> invalid JSON response") from e
        if not isinstance(payload, dict):
            raise CloudAPIError(f"GET {path} -> unexpected response shape ({type(payload).__name__})")
        return payload.get("result")

    def list_organizations(self) -> list[dict]:
        return self._get("/organizations") or []

    def list_services(self, org_id: str) -> list[dict]:
        return self._get(f"/organizations/{org_id}/services") or []

    def get_service(self, org_id: str, service_id: str) -> dict:
        return self._get(f"/organizations/{org_id}/services/{service_id}")

    def get_usage_cost(self, org_id: str, from_date: str, to_date: str) -> dict:
        """Fetch the actual billed usage-cost report for a date window.

        Dates are ISO ``YYYY-MM-DD``. Returns the raw result object
        (``grandTotalCHC`` + per-entity/day ``costs`` records, each carrying
        ``organizationTier`` and a ``metrics`` breakdown in ClickHouse Credits).
        """
        path = f"/organizations/{org_id}/usageCost?from_date={from_date}&to_date={to_date}"
        return self._get(path) or {}

    @staticmethod
    def summarize_usage_cost(usage: dict, service_id: Optional[str] = None) -> dict[str, Any]:
        """Aggregate a usageCost report into tier + total cost (in CHC ≈ USD).

        If ``service_id`` is given, only that service's records are summed;
        otherwise the whole org. Tier is taken from the records'
        ``organizationTier`` (Basic/Scale/Enterprise) — the only API surface
        that exposes the plan.
        """
        records = usage.get("costs", []) or []
        if service_id:
            records = [r for r in records if r.get("serviceId") == service_id or r.get("entityId") == service_id]

        # Sum the metric buckets across the window, take the plan tier from the
        # most recent record (the plan can change over the window; billed records
        # also lag real time, so we report the as-of date), and count how many
        # distinct days fell under each tier so a tier change is visible and the
        # estimate can be weighted accordingly.
        totals: dict[str, float] = {}
        tier_rec = None
        tier_dates: dict[str, set] = {}
        for r in records:
            for k, v in (r.get("metrics") or {}).items():
                if isinstance(v, (int, float)):
                    totals[k] = totals.get(k, 0.0) + v
            t = r.get("organizationTier")
            if t:
                tier_dates.setdefault(t.lower(), set()).add(r.get("date"))
                if tier_rec is None or (r.get("date") or "") > (tier_rec.get("date") or ""):
                    tier_rec = r
        org_tier = tier_rec.get("organizationTier") if tier_rec else None
        tier_as_of = tier_rec.get("date") if tier_rec else None
        tier_days = {t: len(dates) for t, dates in tier_dates.items()}

        # Classify each metric into one bucket, case-insensitively, so a re-cased key (e.g.
        # "ComputeCHC", "dataTransferCHC") still lands where it belongs. `other` is the catch-all for
        # everything not otherwise named (ClickPipes, dictionary, future/renamed metrics), so the total
        # always reflects the whole `metrics` map — a mis-bucketed key can shift the breakdown but never
        # drops from the total.
        _exact = {"computechc": "compute", "storagechc": "storage", "backupchc": "backup", "initialloadchc": "transfer"}
        buckets = {"compute": 0.0, "storage": 0.0, "backup": 0.0, "transfer": 0.0, "other": 0.0}
        for k, v in totals.items():
            kl = k.lower()
            if kl in _exact:
                buckets[_exact[kl]] += v
            elif "datatransferchc" in kl:
                buckets["transfer"] += v
            else:
                buckets["other"] += v
        compute, storage, backup, transfer, other = (
            buckets["compute"],
            buckets["storage"],
            buckets["backup"],
            buckets["transfer"],
            buckets["other"],
        )
        total = round(compute + storage + backup + transfer + other, 6)

        # True TCO: the org-wide grand total across ALL services plus org-level charges (backups,
        # ClickPipes, shared costs) that aren't attributed to any single service. This is >= the
        # per-service sum above; the gap is org-level spend the serviceId filter can't see.
        grand_total = usage.get("grandTotalCHC")
        org_total = round(float(grand_total), 6) if isinstance(grand_total, (int, float)) else None

        return {
            "tier": org_tier.lower() if isinstance(org_tier, str) else None,
            "organization_tier_raw": org_tier,
            "tier_as_of_date": tier_as_of,
            "tier_days": tier_days,
            "record_count": len(records),
            "actual_cost_chc": {
                "compute": round(compute, 6),
                "storage": round(storage, 6),
                "backup": round(backup, 6),
                "data_transfer": round(transfer, 6),
                "other": round(other, 6),
                "total": total,
            },
            # ClickHouse Credits are denominated 1 CHC = 1 USD (before discounts).
            "actual_total_usd": total,
            # Org-wide grand total (all services + org-level charges) = true TCO. May exceed
            # actual_total_usd, which is scoped to the profiled service.
            "org_total_usd": org_total,
        }

    def discover_service(
        self,
        org_id: Optional[str] = None,
        service_id: Optional[str] = None,
        host: Optional[str] = None,
    ) -> dict[str, Any]:
        """Locate a single service and return its normalized metadata.

        Resolution order:
          - organization: explicit ``org_id``; else the sole org if there is
            exactly one (error otherwise — set ``cloud_api.organization_id``).
          - service: explicit ``service_id``; else the service whose HTTPS
            endpoint matches ``host``; else the sole service if there is exactly
            one (error otherwise — set ``cloud_api.service_id``).
        """
        if not org_id:
            orgs = self.list_organizations()
            if len(orgs) != 1:
                raise CloudAPIError(
                    f"Expected exactly 1 organization, found {len(orgs)}; " "set cloud_api.organization_id in config"
                )
            org_id = orgs[0]["id"]

        services = self.list_services(org_id)
        svc = None
        if service_id:
            svc = next((s for s in services if s.get("id") == service_id), None)
            if svc is None:
                raise CloudAPIError(f"Service {service_id} not found in organization {org_id}")
        elif host:
            # Normalize both sides so a case-only mismatch still resolves the service.
            want = host.strip().lower()
            svc = next(
                (
                    s
                    for s in services
                    if any(want == str(ep.get("host") or "").strip().lower() for ep in (s.get("endpoints") or []))
                ),
                None,
            )

        if svc is None:
            if len(services) == 1:
                svc = services[0]
            else:
                raise CloudAPIError(
                    f"Could not uniquely resolve a service (found {len(services)}); "
                    "set cloud_api.service_id in config"
                )

        return normalize_service(svc, org_id)
