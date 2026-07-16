"""Cost attribution collector: attributes ClickHouse usage across 4 buckets — storage, compute,
scan, and ingestion — from ``system.query_log``, ``system.parts``, and
``system.asynchronous_insert_log``. Compute is weighted by
``query_duration_ms * greatest(peak_threads_usage, 1)``.

Dollar figures are **actual billed cost only**, pulled from the ClickHouse Cloud ``usageCost`` API
when Cloud API credentials are configured — there is no synthetic rate-card estimation. The plan
tier (Basic/Scale/Enterprise) and provider/region are recorded as metadata from the same API but do
not drive any pricing math. On self-managed (OSS) — or Cloud without API credentials — no dollars are
produced; a resource footprint plus the full usage attribution is reported instead. For Cloud, uses
``clusterAllReplicas()`` to capture all replicas.
"""

from typing import Any
from databricks.labs.lakebridge.resources.assessments.clickhouse import CLICKHOUSE_CLOUD_HOST_SUFFIX
from databricks.labs.lakebridge.resources.assessments.clickhouse.collectors.base import BaseCollector


class CostsCollector(BaseCollector):
    name = "costs"
    description = "Monthly cost attribution: compute, storage, scan, and ingestion by database, user, and query type"

    # Cloud API service metadata is fetched once and cached (see _get_cloud_metadata).
    _cloud_meta_cached: bool = False
    _cloud_meta: dict | None = None

    def collect(self) -> dict[str, Any]:
        d = self.days_back
        results: dict[str, Any] = {}

        # When Cloud API credentials are configured, fetch authoritative service metadata (provider,
        # region, real compute sizing, actual billed cost, plan tier) from the Cloud API. Region is
        # still resolved for metadata; there is no rate card, so it drives no pricing math.
        cloud_meta = self._get_cloud_metadata()
        region = self._detect_region(cloud_meta)
        region_source = (
            "config"
            if self.config.get("region")
            else "cloud_api" if cloud_meta and cloud_meta.get("region_key") not in (None, "default") else "hostname"
        )
        # Plan tier (Basic/Scale/Enterprise) recorded as metadata: explicit config override >
        # Cloud API organizationTier (from usageCost) > unknown. No pricing depends on it.
        tier = self.config.get("tier") or (cloud_meta or {}).get("tier")
        tier_source = (
            "config"
            if self.config.get("tier")
            else (cloud_meta or {}).get("tier_source") if (cloud_meta or {}).get("tier") else "unknown"
        )
        # Cloud vs self-managed (OSS). Actual dollar cost comes only from the Cloud usageCost API;
        # for OSS (or Cloud without API creds) we report a resource footprint + attribution telemetry.
        is_cloud = self._detect_cloud()

        # Source table expression: use clusterAllReplicas on Cloud
        ql_source = "clusterAllReplicas('default', system.query_log)" if is_cloud else "system.query_log"
        parts_source = "system.parts"  # parts is replicated on Cloud
        skip_settings = "SETTINGS skip_unavailable_shards = 1" if is_cloud else ""

        if is_cloud:
            results["pricing_config"] = {
                "region_detected": region,
                "region_source": region_source,
                "tier": tier,
                "tier_source": tier_source,
                "is_cloud": True,
                "note": "Dollar figures are the actual billed cost from the ClickHouse Cloud usageCost "
                "API (below), reported only when Cloud API credentials are configured. No rate-card "
                "estimation is performed. Provider/region/tier are recorded as metadata.",
            }
        else:
            results["pricing_config"] = {
                "is_cloud": False,
                "deployment": "self-managed (OSS)",
                "cost_estimation": "not_applicable",
                "note": "Self-managed ClickHouse detected — ClickHouse Cloud metered pricing does not apply "
                "(cost is your own VM/bare-metal + disk infrastructure), so no dollar figures are "
                "produced. The resource footprint and all usage attribution (storage by table/db, "
                "compute weight, scan efficiency, ingestion) are still reported.",
            }
        if is_cloud and cloud_meta:
            results["pricing_config"]["cloud_service"] = {
                "service_id": cloud_meta.get("service_id"),
                "name": cloud_meta.get("name"),
                "provider": cloud_meta.get("provider"),
                "region": cloud_meta.get("region"),
                "state": cloud_meta.get("state"),
                "profile": cloud_meta.get("profile"),
                "min_replica_memory_gb": cloud_meta.get("min_replica_memory_gb"),
                "num_replicas": cloud_meta.get("num_replicas"),
                # Tier is absent from the service object but exposed as organizationTier in the
                # usageCost report (see _get_cloud_metadata). Recorded as metadata only.
                "tier": cloud_meta.get("tier"),
                "tier_source": cloud_meta.get("tier_source"),
                "tier_as_of_date": cloud_meta.get("tier_as_of_date"),
                "tier_days": (cloud_meta.get("actual_cost") or {}).get("tier_days"),
                "tier_note": "Plan tier (Basic/Scale/Enterprise) is read from organizationTier in the "
                "Cloud usageCost API, which reflects BILLED history and can lag real time "
                "(and won't show a same-day plan change). For the current tier, set "
                "config.tier explicitly — it takes precedence over the API. tier_days shows "
                "the per-tier day counts when the plan changed during the window.",
            }
            # Actual billed cost from the usageCost API — the only dollar figure this collector emits.
            actual = cloud_meta.get("actual_cost") or {}
            if actual.get("record_count"):
                window_days = (cloud_meta.get("actual_cost_window_days")) or min(self.days_back, 30)
                results["pricing_config"]["actual_billed_cost"] = {
                    "source": "cloud_usage_cost_api",
                    "window_days": window_days,
                    "currency": "CHC (1 CHC ≈ 1 USD, before discounts)",
                    "breakdown_chc": actual.get("actual_cost_chc"),
                    "total_usd": actual.get("actual_total_usd"),
                    "monthly_total_usd": (
                        round(actual["actual_total_usd"] * (30 / max(window_days, 1)), 2)
                        if actual.get("actual_total_usd")
                        else None
                    ),
                    "note": "Real billed cost for the window (authoritative), plus a 30-day-normalized "
                    "monthly figure. Reflects actual tier changes/scaling day-by-day.",
                }

        # ============================================================
        # BUCKET 1: STORAGE ATTRIBUTION (system.parts)
        # ============================================================

        results["storage_by_table"] = self.safe_query(
            "storage_by_table",
            f"""
            SELECT
                database,
                table,
                sum(bytes_on_disk) AS bytes_on_disk,
                sum(data_compressed_bytes) AS compressed_bytes,
                sum(data_uncompressed_bytes) AS uncompressed_bytes,
                sum(rows) AS rows,
                round(sum(data_uncompressed_bytes) / nullIf(sum(data_compressed_bytes), 0), 2) AS compression_ratio,
                count() AS active_parts
            FROM {parts_source}
            WHERE active = 1
              AND database NOT IN ('system', 'information_schema', 'INFORMATION_SCHEMA')
            GROUP BY database, table
            ORDER BY bytes_on_disk DESC
            """,
        )

        # Enrich with GB footprint (applies to both Cloud and OSS; no dollar estimation).
        if results["storage_by_table"]:
            for row in results["storage_by_table"]:
                compressed_gb = (row.get("compressed_bytes") or 0) / (1024**3)
                disk_gb = (row.get("bytes_on_disk") or 0) / (1024**3)
                row["compressed_gb"] = round(compressed_gb, 6)
                row["disk_gb"] = round(disk_gb, 6)

        results["storage_by_database"] = self.safe_query(
            "storage_by_database",
            f"""
            SELECT
                database,
                sum(bytes_on_disk) AS bytes_on_disk,
                sum(data_compressed_bytes) AS compressed_bytes,
                sum(data_uncompressed_bytes) AS uncompressed_bytes,
                sum(rows) AS total_rows,
                count(DISTINCT table) AS table_count
            FROM {parts_source}
            WHERE active = 1
              AND database NOT IN ('system', 'information_schema', 'INFORMATION_SCHEMA')
            GROUP BY database
            ORDER BY bytes_on_disk DESC
            """,
        )
        if results["storage_by_database"]:
            for row in results["storage_by_database"]:
                compressed_gb = (row.get("compressed_bytes") or 0) / (1024**3)
                row["compressed_gb"] = round(compressed_gb, 6)

        results["storage_total"] = {
            "total_compressed_gb": round(
                sum(r.get("compressed_gb", 0) for r in (results.get("storage_by_table") or [])), 6
            ),
            "total_disk_gb": round(sum(r.get("disk_gb", 0) for r in (results.get("storage_by_table") or [])), 6),
        }

        # ============================================================
        # BUCKET 2: COMPUTE ATTRIBUTION (query_log)
        # compute_weight = query_duration_ms * greatest(peak_threads_usage, 1)
        # ============================================================

        # By user
        results["compute_by_user"] = self.safe_query(
            "compute_by_user",
            f"""
            SELECT
                user,
                count() AS runs,
                sum(query_duration_ms * greatest(peak_threads_usage, 1)) AS compute_weight,
                sum(query_duration_ms) AS total_duration_ms,
                sum(read_bytes) AS total_read_bytes,
                sum(written_bytes) AS total_written_bytes,
                max(memory_usage) AS max_memory_usage,
                avg(memory_usage) AS avg_memory_usage
            FROM {ql_source}
            WHERE type = 'QueryFinish'
              AND is_initial_query = 1
              AND event_time >= now() - INTERVAL {d} DAY
            GROUP BY user
            ORDER BY compute_weight DESC
            {skip_settings}
            """,
        )

        # By database
        results["compute_by_database"] = self.safe_query(
            "compute_by_database",
            f"""
            SELECT
                db,
                count() AS runs,
                sum(query_duration_ms * greatest(peak_threads_usage, 1)) AS compute_weight,
                sum(query_duration_ms) AS total_duration_ms,
                sum(read_bytes) AS total_read_bytes,
                sum(written_bytes) AS total_written_bytes
            FROM {ql_source}
            ARRAY JOIN databases AS db
            WHERE type = 'QueryFinish'
              AND is_initial_query = 1
              AND event_time >= now() - INTERVAL {d} DAY
              AND db NOT IN ('system', 'information_schema', 'INFORMATION_SCHEMA')
            GROUP BY db
            ORDER BY compute_weight DESC
            {skip_settings}
            """,
        )

        # By query type
        results["compute_by_query_type"] = self.safe_query(
            "compute_by_query_type",
            f"""
            SELECT
                query_kind,
                count() AS runs,
                sum(query_duration_ms * greatest(peak_threads_usage, 1)) AS compute_weight,
                sum(query_duration_ms) AS total_duration_ms,
                sum(read_bytes) AS total_read_bytes,
                sum(written_bytes) AS total_written_bytes,
                avg(memory_usage) AS avg_memory_usage
            FROM {ql_source}
            WHERE type = 'QueryFinish'
              AND is_initial_query = 1
              AND event_time >= now() - INTERVAL {d} DAY
            GROUP BY query_kind
            ORDER BY compute_weight DESC
            {skip_settings}
            """,
        )

        # Top 30 most expensive query families
        results["top_expensive_queries"] = self.safe_query(
            "top_expensive_queries",
            f"""
            SELECT
                normalized_query_hash,
                any(query_kind) AS query_kind,
                any(user) AS sample_user,
                count() AS runs,
                sum(query_duration_ms * greatest(peak_threads_usage, 1)) AS compute_weight,
                sum(read_bytes) AS total_read_bytes,
                sum(written_bytes) AS total_written_bytes,
                max(memory_usage) AS max_memory_usage,
                quantileTDigest(0.95)(query_duration_ms) AS p95_ms,
                quantileTDigest(0.99)(query_duration_ms) AS p99_ms,
                any(query) AS sample_query
            FROM {ql_source}
            WHERE type = 'QueryFinish'
              AND is_initial_query = 1
              AND event_time >= now() - INTERVAL {d} DAY
            GROUP BY normalized_query_hash
            ORDER BY compute_weight DESC
            LIMIT 30
            {skip_settings}
            """,
        )
        # Truncate sample queries for readability / redaction.
        if results["top_expensive_queries"]:
            for row in results["top_expensive_queries"]:
                if row.get("sample_query"):
                    row["sample_query"] = row["sample_query"][:300]

        # Hourly cost pattern
        results["compute_by_hour"] = self.safe_query(
            "compute_by_hour",
            f"""
            SELECT
                toHour(event_time) AS hour_of_day,
                count() AS runs,
                sum(query_duration_ms * greatest(peak_threads_usage, 1)) AS compute_weight,
                sum(query_duration_ms) AS total_duration_ms,
                avg(memory_usage) AS avg_memory_usage
            FROM {ql_source}
            WHERE type = 'QueryFinish'
              AND is_initial_query = 1
              AND event_time >= now() - INTERVAL {d} DAY
            GROUP BY hour_of_day
            ORDER BY hour_of_day
            {skip_settings}
            """,
        )

        # Daily trend
        results["compute_by_day"] = self.safe_query(
            "compute_by_day",
            f"""
            SELECT
                toDate(event_time) AS day,
                count() AS runs,
                sum(query_duration_ms * greatest(peak_threads_usage, 1)) AS compute_weight,
                sum(read_bytes) AS total_read_bytes,
                sum(written_bytes) AS total_written_bytes
            FROM {ql_source}
            WHERE type = 'QueryFinish'
              AND is_initial_query = 1
              AND event_time >= now() - INTERVAL {d} DAY
            GROUP BY day
            ORDER BY day
            {skip_settings}
            """,
        )

        # ============================================================
        # BUCKET 3: SCAN-COST ATTRIBUTION
        # "Which queries are expensive because they scan a lot?"
        # ============================================================

        results["top_scan_heavy_queries"] = self.safe_query(
            "top_scan_heavy_queries",
            f"""
            SELECT
                normalized_query_hash,
                count() AS runs,
                sum(read_bytes) AS total_read_bytes,
                avg(read_bytes) AS avg_read_bytes,
                sum(read_rows) AS total_read_rows,
                sum(result_rows) AS total_result_rows,
                quantileTDigest(0.95)(query_duration_ms) AS p95_ms,
                quantileTDigest(0.99)(query_duration_ms) AS p99_ms,
                any(query) AS sample_query
            FROM {ql_source}
            WHERE type = 'QueryFinish'
              AND is_initial_query = 1
              AND event_time >= now() - INTERVAL {d} DAY
            GROUP BY normalized_query_hash
            ORDER BY total_read_bytes DESC
            LIMIT 30
            {skip_settings}
            """,
        )
        if results["top_scan_heavy_queries"]:
            for row in results["top_scan_heavy_queries"]:
                if row.get("sample_query"):
                    row["sample_query"] = row["sample_query"][:300]
                # Waste indicator: high scan, low result
                read_rows = row.get("total_read_rows", 0) or 0
                result_rows = row.get("total_result_rows", 0) or 0
                row["scan_efficiency_pct"] = round(result_rows / max(read_rows, 1) * 100, 2)

        # ============================================================
        # BUCKET 4: INGESTION-COST ATTRIBUTION
        # ============================================================

        # Sync inserts from query_log
        results["ingestion_by_table"] = self.safe_query(
            "ingestion_by_table",
            f"""
            SELECT
                tbl,
                count() AS insert_count,
                sum(written_bytes) AS total_written_bytes,
                sum(written_rows) AS total_written_rows,
                sum(query_duration_ms) AS total_duration_ms
            FROM {ql_source}
            ARRAY JOIN tables AS tbl
            WHERE type = 'QueryFinish'
              AND query_kind = 'Insert'
              AND is_initial_query = 1
              AND event_time >= now() - INTERVAL {d} DAY
              AND NOT startsWith(tbl, 'system.')
              AND NOT startsWith(tbl, 'information_schema.')
            GROUP BY tbl
            ORDER BY total_written_bytes DESC
            {skip_settings}
            """,
        )

        # Async inserts (if available)
        results["async_ingestion_by_table"] = self.safe_query(
            "async_ingestion_by_table",
            f"""
            SELECT
                database,
                table,
                sum(bytes) AS async_inserted_bytes,
                sum(rows) AS async_inserted_rows,
                count() AS async_insert_batches
            FROM system.asynchronous_insert_log
            WHERE event_time >= now() - INTERVAL {d} DAY
              AND status = 'Ok'
            GROUP BY database, table
            ORDER BY async_inserted_bytes DESC
            """,
        )

        # ============================================================
        # WASTE INDICATORS
        # ============================================================

        results["waste_indicators"] = self.safe_query(
            "waste_indicators",
            f"""
            SELECT
                normalized_query_hash,
                any(user) AS sample_user,
                count() AS runs,
                sum(read_bytes) AS total_read_bytes,
                sum(read_rows) AS total_read_rows,
                sum(result_rows) AS total_result_rows,
                round(sum(result_rows) / nullIf(sum(read_rows), 0) * 100, 2) AS scan_efficiency_pct,
                sum(query_duration_ms * greatest(peak_threads_usage, 1)) AS compute_weight,
                any(query) AS sample_query
            FROM {ql_source}
            WHERE type = 'QueryFinish'
              AND is_initial_query = 1
              AND event_time >= now() - INTERVAL {d} DAY
              AND read_rows > 1000
            GROUP BY normalized_query_hash
            HAVING scan_efficiency_pct < 1 AND runs >= 2
            ORDER BY compute_weight DESC
            LIMIT 20
            {skip_settings}
            """,
        )
        if results["waste_indicators"]:
            for row in results["waste_indicators"]:
                if row.get("sample_query"):
                    row["sample_query"] = row["sample_query"][:300]

        # ============================================================
        # PROPORTIONAL COST ALLOCATION
        # ============================================================

        # Compute-weight totals (valid for both Cloud and OSS).
        total_cw_by_user = sum(r.get("compute_weight", 0) for r in (results.get("compute_by_user") or []))
        total_cw_by_db = sum(r.get("compute_weight", 0) for r in (results.get("compute_by_database") or []))
        active_hours = len(results.get("compute_by_hour") or [])  # hours of day with queries
        total_query_minutes = (
            sum(r.get("total_duration_ms", 0) for r in (results.get("compute_by_user") or [])) / 60_000
        )

        # Always attribute each user's/db's share of compute weight — this is a
        # usage distribution and applies regardless of deployment.
        if results.get("compute_by_user") and total_cw_by_user > 0:
            for row in results["compute_by_user"]:
                row["compute_weight_pct"] = round(row["compute_weight"] / total_cw_by_user * 100, 2)
        if results.get("compute_by_database") and total_cw_by_db > 0:
            for row in results["compute_by_database"]:
                row["compute_weight_pct"] = round(row["compute_weight"] / max(total_cw_by_db, 1) * 100, 2)

        # Resource footprint — reported for both Cloud and OSS. There is no synthetic dollar
        # estimation; actual Cloud spend (when available) lives in pricing_config.actual_billed_cost.
        results["resource_footprint"] = {
            "deployment": "cloud" if is_cloud else "self-managed (OSS)",
            "total_compressed_gb": results["storage_total"]["total_compressed_gb"],
            "total_disk_gb": results["storage_total"]["total_disk_gb"],
            "analysis_period_days": d,
            "total_compute_weight": total_cw_by_user,
            "total_query_minutes": round(total_query_minutes, 2),
            "active_query_hours_of_day": active_hours,
            "note": "Resource footprint (disk GB, compute-weight distribution, query activity) plus the "
            "per-table/db/user usage attribution above. On Cloud, the actual billed cost is in "
            "pricing_config.actual_billed_cost (when Cloud API credentials are configured); on "
            "self-managed / OSS, use this footprint to size against your own infrastructure.",
        }

        return results

    def _detect_cloud(self) -> bool:
        """Return True for ClickHouse Cloud, False for self-managed (OSS).

        Uses the same rule as ``variants.resolve_clickhouse_variant`` so the two never disagree: a
        hostname ending in ``.clickhouse.cloud`` is a definitive yes; otherwise the authoritative
        ``cloud_mode`` server setting decides (``1`` on Cloud; absent on older OSS builds → OSS).
        """
        host = str(self.config.get("host") or "").strip().lower()
        if host.endswith(CLICKHOUSE_CLOUD_HOST_SUFFIX):
            return True
        rows = self.safe_query(
            "cloud_mode_check",
            "SELECT value FROM system.settings WHERE name = 'cloud_mode'",
        )
        if rows:
            return str(rows[0].get("value", "")).strip().lower() in ("1", "true")
        return False

    # Provider tokens as they appear in ClickHouse Cloud service hostnames.
    _CLOUD_PROVIDERS = ("aws", "gcp", "azure")

    def _get_cloud_metadata(self) -> dict | None:
        """Fetch authoritative service metadata from the ClickHouse Cloud API.

        Reads credentials from ``config["cloud_api"]`` (``key_id``,
        ``key_secret``, optional ``organization_id`` / ``service_id``). Returns
        the normalized service dict, or ``None`` if no credentials are
        configured or the API call fails (the estimate then falls back to
        hostname parsing). Cached after the first call.
        """
        if getattr(self, "_cloud_meta_cached", False):
            return self._cloud_meta
        self._cloud_meta_cached = True
        self._cloud_meta = None

        api_cfg = self.config.get("cloud_api") or {}
        key_id = api_cfg.get("key_id")
        key_secret = api_cfg.get("key_secret")
        if not (key_id and key_secret):
            return None

        try:
            from datetime import datetime, timedelta, timezone
            from databricks.labs.lakebridge.resources.assessments.clickhouse.cloud_api import ClickHouseCloudAPI

            api = ClickHouseCloudAPI(key_id, key_secret)
            meta = api.discover_service(
                org_id=api_cfg.get("organization_id"),
                service_id=api_cfg.get("service_id"),
                host=self.config.get("host"),
            )
            # Enrich with the usageCost report: authoritative plan tier
            # (organizationTier) + actual billed cost for the analysis window.
            # The API allows at most a 31-day span, so the cost window is capped
            # at 30 days. The tier is stable org-level info; if the (possibly
            # short) analysis window has no billing records, widen to the full
            # 30-day lookback just to resolve the tier.
            try:
                today = datetime.now(timezone.utc).date()
                cost_days = min(self.days_back, 30)
                usage = api.get_usage_cost(
                    meta["organization_id"], (today - timedelta(days=cost_days)).isoformat(), today.isoformat()
                )
                summary = api.summarize_usage_cost(usage, service_id=meta.get("service_id"))

                if not summary.get("tier") and cost_days < 30:
                    wide = api.get_usage_cost(
                        meta["organization_id"], (today - timedelta(days=30)).isoformat(), today.isoformat()
                    )
                    wide_summary = api.summarize_usage_cost(wide, service_id=meta.get("service_id"))
                    if wide_summary.get("tier"):
                        summary["tier"] = wide_summary["tier"]
                        summary["tier_as_of_date"] = wide_summary.get("tier_as_of_date")

                if summary.get("tier"):
                    meta["tier"] = summary["tier"]  # overrides the null from the service object
                    meta["tier_source"] = "usage_cost"
                    meta["tier_as_of_date"] = summary.get("tier_as_of_date")
                meta["actual_cost"] = summary
                meta["actual_cost_window_days"] = cost_days
            except Exception as e:
                self.errors.append(f"[{self.name}] cloud_api usageCost: {str(e)[:200]}")
            self._cloud_meta = meta
        except Exception as e:
            self.errors.append(f"[{self.name}] cloud_api: {str(e)[:200]}")
            self._cloud_meta = None
        return self._cloud_meta

    def _detect_region(self, cloud_meta: dict | None = None) -> str:
        """Detect the ClickHouse Cloud ``provider:region``.

        Resolution order:
          1. Explicit ``config["region"]`` (manual override).
          2. Cloud API service metadata, when available (authoritative).
          3. The service hostname, parsed as
             ``<service-id>.<region>.<provider>.clickhouse.cloud`` —
             e.g. ``abc123.us-east-1.aws.clickhouse.cloud``,
             ``abc123.us-central1.gcp.clickhouse.cloud``,
             ``abc123.eastus2.azure.clickhouse.cloud``. Parsing the provider and
             region tokens directly means AWS, GCP, and Azure are all detected.
          4. ``"default"``.
        """
        config_region = self.config.get("region")
        if config_region:
            return config_region

        if cloud_meta and cloud_meta.get("region_key") not in (None, "default"):
            return cloud_meta["region_key"]

        host = self.config.get("host", "")
        if "clickhouse.cloud" in host:
            prefix = host.split(".clickhouse.cloud", 1)[0]
            tokens = prefix.split(".")
            if len(tokens) >= 3:
                provider = tokens[-1].lower()
                region = tokens[-2].lower()
                if provider in self._CLOUD_PROVIDERS:
                    return f"{provider}:{region}"
            # Legacy hosts without a provider token
            # (<service-id>.<region>.clickhouse.cloud): default to AWS, as before.
            if len(tokens) >= 2:
                return f"aws:{tokens[-1].lower()}"

        return "default"
