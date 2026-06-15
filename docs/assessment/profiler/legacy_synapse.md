# Legacy Synapse Profiler Details

* [Prerequisites](#prerequisites)
* [Profiled Tables and Views](#profiled-tables-and-views)
* [Configure Connection to Legacy Synapse](#configure-connection-to-legacy-synapse)
* [Execute the Profiler](#execute-the-profiler)

info

This profiler targets **Azure Synapse dedicated SQL pool** (formerly Azure SQL Data Warehouse). For serverless SQL pools, Spark pools, and the Synapse workspace control plane, use the [Synapse profiler](/lakebridge/docs/assessment/profiler/synapse.md) instead.

## Prerequisites[​](#prerequisites "Direct link to Prerequisites")

### 1. Download[​](#1-download "Direct link to 1. Download")

* **ODBC driver for SQL Server** — required for `pyodbc` to talk to the dedicated SQL pool. [Download](https://learn.microsoft.com/en-us/sql/connect/odbc/download-odbc-driver-for-sql-server?view=sql-server-ver18).
* **(Windows only) Microsoft Visual C++ Redistributable (x64)** — required by `pyodbc` and the ODBC driver, both of which are native binaries linked against the MSVC runtime. [Download](https://learn.microsoft.com/en-us/cpp/windows/latest-supported-vc-redist?view=msvc-170).

### 2. Authentication[​](#2-authentication "Direct link to 2. Authentication")

Attention:

The profiler currently authenticates via **SQL Authentication** only. Please use a SQL login with MFA disabled.

### 3. Required Database Permissions[​](#3-required-database-permissions "Direct link to 3. Required Database Permissions")

The SQL user configured for the profiler must have read access to the system catalog views and PDW Dynamic Management Views listed in [Profiled Tables and Views](#profiled-tables-and-views).

Connect to the target dedicated SQL pool as an admin and run:

```sql
-- Create a contained user from the server login
CREATE USER [<user_name>] FROM LOGIN [<user_name>];

-- Grant access to PDW Dynamic Management Views (sys.dm_pdw_*)
GRANT VIEW DATABASE STATE TO [<user_name>];

-- Grant access to object definitions (routines, views)
GRANT VIEW DEFINITION TO [<user_name>];

-- Grant read access to INFORMATION_SCHEMA views
GRANT SELECT ON SCHEMA::INFORMATION_SCHEMA TO [<user_name>];

```

tip

The profiler queries server-level catalog views such as `sys.databases` from the target database context. `VIEW DATABASE STATE` covers the `sys.dm_pdw_*` DMVs used to extract sessions, requests, and per-node storage statistics.

## Profiled Tables and Views[​](#profiled-tables-and-views "Direct link to Profiled Tables and Views")

The profiler executes queries against the following system tables and PDW DMVs. The results are organized into two extraction steps: **schema metadata** and **activity metrics**.

### Schema Metadata[​](#schema-metadata "Direct link to Schema Metadata")

| Query     | Source Table(s)               | Description                                                                                            |
| --------- | ----------------------------- | ------------------------------------------------------------------------------------------------------ |
| Databases | `sys.databases`               | Lists each database by name.                                                                           |
| Tables    | `INFORMATION_SCHEMA.TABLES`   | Extracts table definitions and types from the dedicated SQL pool.                                      |
| Views     | `INFORMATION_SCHEMA.VIEWS`    | Extracts view definitions (SQL text is redacted for security).                                         |
| Columns   | `INFORMATION_SCHEMA.COLUMNS`  | Column-level metadata including data types, nullability, precision, collation, and domain information. |
| Routines  | `INFORMATION_SCHEMA.ROUTINES` | Stored procedures and user-defined functions (routine definitions are redacted for security).          |

### Activity Metrics[​](#activity-metrics "Direct link to Activity Metrics")

| Query        | Source Table(s)                       | Description                                                                                                                          |
| ------------ | ------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------ |
| Requests     | `sys.dm_pdw_exec_requests`            | Recently executed PDW requests with submit/start/end times, total elapsed time, status, resource class, and the originating command. |
| Sessions     | `sys.dm_pdw_exec_sessions`            | Active user sessions with login info, app names, query counts, and transaction state. System sessions are excluded.                  |
| Storage Info | `sys.dm_pdw_nodes_db_partition_stats` | Per-compute-node storage usage: reserved and used space (MB), aggregated across all partitions on each node.                         |

## Configure Connection to Legacy Synapse[​](#configure-connection-to-legacy-synapse "Direct link to Configure Connection to Legacy Synapse")

Run the following command to configure the profiler connection to your dedicated SQL pool:

```console
databricks labs lakebridge configure-database-profiler

Please select the source system you want to configure
[0] synapse
[1] mssql
[2] legacy_synapse
Enter a number between 0 and 2: 2

(local | env)
local means values are read as plain text
env means values are read from environment variables, and fall back to plain text if not variable is not found

Enter secret vault type (local | env)
[0] env
[1] local
Enter a number between 0 and 1: 1
Enter fetch size (default: 1000):
Enter login timeout (seconds) (default: 30):
Enter the fully-qualified server name: my-dw-server.database.windows.net
Enter the port details: 1433
Enter the database name: my_pool
Enter the SQL username: profiler_user
Enter the SQL password:
Trust server certificate (default: no):
Enter timezone (e.g. America/New_York) (default: UTC):
Enter the ODBC driver installed locally (default: ODBC Driver 18 for SQL Server):

```

### Configuration Parameters[​](#configuration-parameters "Direct link to Configuration Parameters")

| Parameter                    | Description                                                                                                                                      | Default                         |
| ---------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------ | ------------------------------- |
| **Secret vault type**        | `local` for plain text values, `env` to read from environment variables                                                                          | —                               |
| **Fetch size**               | Number of rows fetched per batch from the source                                                                                                 | `1000`                          |
| **Login timeout**            | Connection timeout in seconds                                                                                                                    | `30`                            |
| **Server name**              | Fully-qualified hostname of the dedicated SQL pool                                                                                               | —                               |
| **Port**                     | Server port number                                                                                                                               | —                               |
| **Database**                 | The dedicated SQL pool name to connect to. Dedicated pools do not have a `master` database, so this must be set to the pool you want to profile. | —                               |
| **SQL username**             | SQL Authentication username                                                                                                                      | —                               |
| **SQL password**             | SQL Authentication password (hidden input)                                                                                                       | —                               |
| **Trust server certificate** | Skip TLS certificate validation when connecting. Leave as `no` for Azure-hosted dedicated SQL pools, which present valid certificates.           | `no`                            |
| **Timezone**                 | Timezone for timestamp normalization                                                                                                             | `UTC`                           |
| **ODBC driver**              | Locally installed ODBC driver name                                                                                                               | `ODBC Driver 18 for SQL Server` |

## Execute the Profiler[​](#execute-the-profiler "Direct link to Execute the Profiler")

Once configured, run the profiler to extract metadata and activity metrics from your dedicated SQL pool:

```bash
databricks labs lakebridge execute-database-profiler --source-tech legacy_synapse

```

The profiler will:

1. Connect to your dedicated SQL pool using the configured credentials
2. Execute the schema metadata and activity metric extraction queries
3. Store the results in a local DuckDB extract file

[Back to Configure Profiler](/lakebridge/docs/assessment/profiler.md#configure-profiler)
