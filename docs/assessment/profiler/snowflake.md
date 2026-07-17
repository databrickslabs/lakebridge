# Snowflake Profiler Details

## Prerequisites[​](#prerequisites "Direct link to Prerequisites")

* A Snowflake user with permission to create Personal Access Tokens (PATs) and to query the `SNOWFLAKE.ACCOUNT_USAGE` schema (e.g., the `ACCOUNTADMIN` role or a role granted `IMPORTED PRIVILEGES ON DATABASE SNOWFLAKE`).
* The `rate_sheet` step also reads `SNOWFLAKE.ORGANIZATION_USAGE.RATE_SHEET_DAILY` for the effective credit rate and account tier. This is available to `ACCOUNTADMIN` in an ORGADMIN-enabled account (the first account in an organization is ORGADMIN-enabled by default, so single-account orgs are covered), or to a custom role granted the `ORGANIZATION_BILLING_VIEWER` database role. If the account isn't ORGADMIN-enabled — or the organization is on a reseller contract — the view returns no rows, so `rate_sheet` is skipped and the rest of the profile still completes.
* Network access from the machine running Lakebridge to your Snowflake account.

## Authentication[​](#authentication "Direct link to Authentication")

The Snowflake profiler authenticates with a **Programmatic Access Token (PAT)**. Follow Snowflake's official guide to generate one:

[Snowflake docs: Programmatic access tokens](https://docs.snowflake.com/en/user-guide/programmatic-access-tokens#generating-a-programmatic-access-token)

You'll need:

* `account` — your Snowflake account identifier (e.g., `myorg-myaccount.snowflakecomputing.com`)
* `user` — Snowflake username
* `role`, `warehouse`, `database`, `schema` — typically `ACCOUNTADMIN`, `COMPUTE_WH`, `SNOWFLAKE`, `ACCOUNT_USAGE`
* The PAT itself (paste when prompted; it's stored under `pat` in the credentials file)

If your account or user has a network policy, also see Snowflake's docs on [bypassing the network policy on a PAT](https://docs.snowflake.com/en/user-guide/programmatic-access-tokens) for short-term testing from a dynamic IP.

## Configure and run the profiler[​](#configure-and-run-the-profiler "Direct link to Configure and run the profiler")

```bash
databricks labs lakebridge configure-database-profiler

```

Pick **snowflake** when prompted, then paste the connection details and PAT.

```bash
databricks labs lakebridge execute-database-profiler

```

The extract is written to a DuckDB file at `~/.databricks/labs/lakebridge_profilers/snowflake_assessment/profiler_extract.db`. It contains raw `SNOWFLAKE.ACCOUNT_USAGE` data — warehouse usage, query history, storage, and (when applicable) pipe / autoclustering / materialized-view refresh credits. By default each extract looks back 90 days.

To keep extracts shareable on high-volume accounts, `query_history` includes metrics and `QUERY_TYPE` for every query in the window but **omits `QUERY_TEXT`**. A separate `query_samples` table holds a random sample of **10,000** rows **with** `QUERY_TEXT` (join to `query_history` on `QUERY_ID` for full metrics). Login history (`user_activity`) is not extracted.

Inspect it with:

```bash
cd ~/.databricks/labs/lakebridge_profilers/snowflake_assessment
duckdb profiler_extract.db "SHOW TABLES;"
duckdb profiler_extract.db "SELECT * FROM warehouse_usage LIMIT 10;"
duckdb profiler_extract.db "SELECT * FROM query_history LIMIT 10;"
duckdb profiler_extract.db "SELECT query_id, query_type, left(query_text, 80) FROM query_samples LIMIT 10;"

```
