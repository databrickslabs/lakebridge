# Oracle Profiler Details

* [Prerequisites](#prerequisites)
* [Profiled Tables and Views](#profiled-tables-and-views)
* [Configure Connection to Oracle](#configure-connection-to-oracle)
* [Execute the Profiler](#execute-the-profiler)

info

The Oracle profiler targets **multitenant container databases (CDB)** and reads from `CDB_*`, `GV$*`, and AWR views. It must be run against `CDB$ROOT`, not an individual PDB.

## Prerequisites[​](#prerequisites "Direct link to Prerequisites")

### 1. Download[​](#1-download "Direct link to 1. Download")

No local Oracle client install is required.

### 2. Network and Authentication[​](#2-network-and-authentication "Direct link to 2. Network and Authentication")

Attention:

The profiler connects with **username and password over TCP** only. The following are **not supported**:

* TCPS / TLS-encrypted listener connections
* Oracle Wallet (mTLS, externally identified users)
* Kerberos / OS authentication
* Multi-factor authentication

The Oracle listener must be reachable from the host running `databricks labs lakebridge` on the configured TNS port.

### 3. Required Database Permissions[​](#3-required-database-permissions "Direct link to 3. Required Database Permissions")

The Oracle user configured for the profiler must be able to read the data dictionary and AWR views listed in [Profiled Tables and Views](#profiled-tables-and-views).

Connect to `CDB$ROOT` as `SYS` (or a DBA-privileged user) and run:

```sql
-- Create a common profiler user (skip if reusing an existing account)
CREATE USER c##lakebridge_profiler IDENTIFIED BY <password> CONTAINER=ALL;
GRANT CREATE SESSION TO c##lakebridge_profiler CONTAINER=ALL;
GRANT SET CONTAINER TO c##lakebridge_profiler CONTAINER=ALL;

-- Catalog and AWR access
GRANT SELECT_CATALOG_ROLE TO c##lakebridge_profiler CONTAINER=ALL;

```

Diagnostic Pack license

The performance queries read from AWR (`CDB_HIST_*`), which requires an Oracle **Diagnostic Pack** license. AWR views return data on any Enterprise Edition database whether or not the pack is licensed — querying them without a license is an audit-time violation, not a runtime error.

By running the profiler against an Oracle source you are asserting that your database is appropriately licensed.

## Profiled Tables and Views[​](#profiled-tables-and-views "Direct link to Profiled Tables and Views")

The Oracle profiler executes queries against the following Oracle data dictionary and AWR views. The results are organized into two extraction steps: **configuration metadata** and **performance metrics**.

### Configuration Metadata[​](#configuration-metadata "Direct link to Configuration Metadata")

| Query            | Source View(s)                                                     | Description                                                                                   |
| ---------------- | ------------------------------------------------------------------ | --------------------------------------------------------------------------------------------- |
| Containers       | `gv$containers`                                                    | CDB and PDB inventory across all RAC instances.                                               |
| DB Features      | `product_component_version`, `gv$instance`, `gv$pdbs`, `gv$osstat` | Database version, instance count, PDB list, and CPU/core/socket counts.                       |
| Instance         | `gv$instance`                                                      | Instance id, name, version, and database type.                                                |
| Memory Evolution | `cdb_hist_parameter`, `cdb_hist_snapshot`                          | Historical SGA/PGA/buffer-cache parameter values across AWR snapshots.                        |
| PDB Objects      | `cdb_objects`, `cdb_users`                                         | Object counts per PDB, owner, and object type (Oracle-maintained schemas excluded).           |
| PDB Partitions   | `cdb_tables`, `cdb_indexes`, `cdb_lobs`                            | Partitioned vs. non-partitioned counts for tables, indexes, and LOBs per PDB and owner.       |
| Storage          | `cdb_data_files`, `cdb_free_space`, `cdb_tablespaces`              | Allocated, free, and max sizes per container, grouped into `SYSTEM`, `UNDO`, and `USER_DATA`. |

### Performance Metrics[​](#performance-metrics "Direct link to Performance Metrics")

| Query                        | Source View(s)                                          | Description                                                                                                                                                     |
| ---------------------------- | ------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| CPU & Waits                  | `cdb_hist_active_sess_history`                          | Hourly wait-event and CPU-time breakdown per PDB and instance from ASH samples.                                                                                 |
| Foreground Session Evolution | `cdb_hist_active_sess_history`, `cdb_users`             | Per-minute distinct foreground session counts by PDB, instance, and end-user.                                                                                   |
| CPU Heatmap                  | `cdb_hist_active_sess_history`, `gv$osstat`             | Hourly Average Active Sessions normalized by CPU core count.                                                                                                    |
| SQL Text                     | `cdb_hist_sqlstat`, `cdb_hist_sqltext`, `audit_actions` | SQL statements from AWR, classified into workload categories (BI/QUERY, ETL, DML, DDL, PL/SQL) with execution counts and total elapsed time per schema and PDB. |

## Configure Connection to Oracle[​](#configure-connection-to-oracle "Direct link to Configure Connection to Oracle")

Run the following command to configure the profiler connection to your Oracle CDB:

```console
databricks labs lakebridge configure-database-profiler

Please select the source system you want to configure
[0] synapse
[1] mssql
[2] oracle
Enter a number between 0 and 2: 2

(local | env)
local means values are read as plain text
env means values are read from environment variables, and fall back to plain text if not variable is not found

Enter secret vault type (local | env)
[0] env
[1] local
Enter a number between 0 and 1: 1
Enter the host details (Server name, IP address, SCAN Name): oracle-host.example.com
Enter the host port number (default: 1521):
Enter the service name (default: orcl): ORCLCDB
Enter user with privileges:
Enter user password:

```

### Configuration Parameters[​](#configuration-parameters "Direct link to Configuration Parameters")

| Parameter             | Description                                                                                                                                                                                          | Default |
| --------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------- |
| **Secret vault type** | `local` for plain text values, `env` to read from environment variables                                                                                                                              | —       |
| **Host**              | Oracle server hostname, IP, or RAC SCAN name                                                                                                                                                         | —       |
| **Port**              | Port the Oracle listener is bound to                                                                                                                                                                 | `1521`  |
| **Service name**      | Service name registered in the Oracle listener (the CDB service)                                                                                                                                     | `orcl`  |
| **User**              | Database user with the grants from [Required Database Permissions](#3-required-database-permissions). `SYSTEM` works out of the box but is over-privileged; the dedicated user above is recommended. | —       |
| **Password**          | Password for the database user (hidden input)                                                                                                                                                        | —       |

## Execute the Profiler[​](#execute-the-profiler "Direct link to Execute the Profiler")

Once configured, run the profiler to extract metadata and performance metrics from your Oracle CDB:

```bash
databricks labs lakebridge execute-database-profiler --source-tech oracle

```

The profiler will:

1. Connect to your Oracle CDB using the configured credentials
2. Execute the configuration and performance metric extraction queries
3. Store the results in a local DuckDB extract file

[Back to Configure Profiler](/lakebridge/docs/assessment/profiler.md#configure-profiler)
