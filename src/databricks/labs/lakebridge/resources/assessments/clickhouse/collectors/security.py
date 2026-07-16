"""Security profiler: users, roles, grants, quotas, sessions."""

from typing import Any
from databricks.labs.lakebridge.resources.assessments.clickhouse.collectors.base import BaseCollector


class SecurityCollector(BaseCollector):
    name = "security"
    description = "Users, roles, grants, quotas, row policies, and session activity"

    def collect(self) -> dict[str, Any]:
        d = self.days_back
        results = {}

        # 1. Users
        results["users"] = self.safe_query(
            "users",
            """
            SELECT
                name,
                storage,
                auth_type,
                auth_params,
                host_ip,
                host_names,
                host_names_regexp,
                host_names_like,
                default_roles_all,
                default_roles_list,
                default_roles_except,
                grantees_any,
                grantees_list,
                grantees_except,
                default_database
            FROM system.users
            ORDER BY name
            """,
        )

        # 2. Roles
        results["roles"] = self.safe_query(
            "roles",
            """
            SELECT name, storage
            FROM system.roles
            ORDER BY name
            """,
        )

        # 3. Role grants
        results["role_grants"] = self.safe_query(
            "role_grants",
            """
            SELECT
                user_name,
                role_name,
                granted_role_name,
                granted_role_is_default,
                with_admin_option
            FROM system.role_grants
            ORDER BY user_name, granted_role_name
            """,
        )

        # 4. Grants (privileges)
        results["grants"] = self.safe_query(
            "grants",
            """
            SELECT
                user_name,
                role_name,
                access_type,
                database,
                table,
                column,
                is_partial_revoke,
                grant_option
            FROM system.grants
            ORDER BY user_name, role_name, database, table
            """,
        )

        # 5. Row policies
        results["row_policies"] = self.safe_query(
            "row_policies",
            """
            SELECT
                name,
                short_name,
                database,
                table,
                id,
                storage,
                select_filter,
                is_restrictive,
                apply_to_all,
                apply_to_list,
                apply_to_except
            FROM system.row_policies
            """,
        )

        # 6. Quotas
        results["quotas"] = self.safe_query(
            "quotas",
            """
            SELECT
                name,
                id,
                storage,
                keys,
                durations,
                apply_to_all,
                apply_to_list,
                apply_to_except
            FROM system.quotas
            """,
        )

        # 7. Quota usage
        results["quota_usage"] = self.safe_query(
            "quota_usage",
            """
            SELECT *
            FROM system.quota_usage
            """,
        )

        # 8. Settings profiles
        results["settings_profiles"] = self.safe_query(
            "settings_profiles",
            """
            SELECT
                name,
                storage,
                num_elements,
                apply_to_all,
                apply_to_list,
                apply_to_except
            FROM system.settings_profiles
            """,
        )

        # 9. Session log (login activity)
        results["session_activity"] = self.safe_query(
            "session_activity",
            f"""
            SELECT
                type,
                user,
                auth_type,
                count() AS event_count,
                uniqExact(client_hostname) AS distinct_hosts,
                min(event_time) AS first_seen,
                max(event_time) AS last_seen
            FROM system.session_log
            WHERE event_time >= now() - INTERVAL {d} DAY
            GROUP BY type, user, auth_type
            ORDER BY event_count DESC
            """,
        )

        # 10. Privileges used in queries
        results["privileges_used"] = self.safe_query(
            "privileges_used",
            f"""
            SELECT
                priv,
                count() AS query_count,
                uniqExact(user) AS distinct_users
            FROM system.query_log
            ARRAY JOIN used_privileges AS priv
            WHERE type = 'QueryFinish'
              AND event_time >= now() - INTERVAL {d} DAY
            GROUP BY priv
            ORDER BY query_count DESC
            LIMIT 50
            """,
        )

        return results
