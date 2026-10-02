# Profiler end-to-end (live-database) credentials

These files let the profiler e2e tests run against real source systems, locally and in CI, with
**nothing secret in the repo**. The filled credentials and key files live in a Unity Catalog
volume; a setup script downloads them to the lakebridge default path before the tests run.

## The 7 sources

| Source           | Section key      | Auth (default)                     | Extra key material         |
|------------------|------------------|------------------------------------|----------------------------|
| Snowflake        | `snowflake`      | `pat` (or `key_pair`)              | `snowflake.pem` (key_pair) |
| Redshift         | `redshift`       | `sql_authentication` (or `iam`)    | —                          |
| Synapse          | `synapse`        | `SqlPassword` (MSSQL driver)       | —                          |
| Legacy Synapse   | `legacy_synapse` | `SqlPassword` (MSSQL driver)       | —                          |
| Oracle           | `oracle`         | user/password                      | —                          |
| Teradata         | `teradata`       | user/password                      | —                          |
| BigQuery         | `bigquery`       | service-account JSON               | `bigquery-sa.json`         |

All seven live in **one** `.credentials.yml` as sections — that is what the credential manager
reads (`get_credentials("<source>")`). See `credentials.template.yml` for the exact shape.

## One-time: populate the volume

Fill `credentials.template.yml`, then upload it (and any key files) to your UC volume:

```bash
VOL=dbfs:/Volumes/<catalog>/<schema>/<volume>/lakebridge-e2e   # dbfs: scheme is required
databricks fs cp .credentials.yml   "$VOL/.credentials.yml"   --overwrite
databricks fs cp snowflake.pem      "$VOL/snowflake.pem"      --overwrite   # only for Snowflake key_pair
databricks fs cp bigquery-sa.json   "$VOL/bigquery-sa.json"   --overwrite   # only for BigQuery
```

Re-run any line after rotating a credential. Access to the volume is governed by Unity Catalog
grants — that is the access control for these secrets.

## Local setup

```bash
export LAKEBRIDGE_E2E_VOLUME=dbfs:/Volumes/<catalog>/<schema>/<volume>/lakebridge-e2e
./setup_e2e_creds.sh                 # or:  ./setup_e2e_creds.sh -p <your-cli-profile>
```

This downloads the files to `~/.databricks/labs/lakebridge/` using your ambient Databricks auth.
For BigQuery locally, also `export GOOGLE_APPLICATION_CREDENTIALS=~/.databricks/labs/lakebridge/bigquery-sa.json`
(the script prints this line).

Snowflake and BigQuery are **opt-in** and never run implicitly — enable each with
`export LAKEBRIDGE_E2E_SNOWFLAKE=1` / `export LAKEBRIDGE_E2E_BIGQUERY=1` (see *Which tests run*).

## CI setup

Add one step before the e2e tests, using the Databricks auth the job already has:

```yaml
- name: Download profiler e2e credentials
  env:
    LAKEBRIDGE_E2E_VOLUME: dbfs:/Volumes/<catalog>/<schema>/<volume>/lakebridge-e2e
    DATABRICKS_HOST: ${{ secrets.DATABRICKS_HOST }}
    DATABRICKS_TOKEN: ${{ secrets.DATABRICKS_TOKEN }}   # or your OAuth env
  run: tests/integration/assessments/e2e/setup_e2e_creds.sh
```

The script writes `GOOGLE_APPLICATION_CREDENTIALS` to `$GITHUB_ENV` for you when the BigQuery key
is present. The tests then read the default credentials path — no code changes needed. To include
the opt-in sources, set `LAKEBRIDGE_E2E_SNOWFLAKE: 1` / `LAKEBRIDGE_E2E_BIGQUERY: 1` in the env of
the test step (not the download step).

## Which tests run

`test_profiler_e2e.py` drives the production CLI entry points in-process (the same functions the
`databricks labs lakebridge` commands dispatch to). Per source it runs:

- **`test-profiler-connection`** — validates connectivity through the real code path.
- **`execute-database-profiler`** — runs the real profiler pipeline, then asserts the DuckDB
  extract exists and its `profiler_run_metadata` row is `COMPLETE` (or `COMPLETE_WITH_ABSENCES`).

Skips are dynamic:

- **No credentials** (`setup_e2e_creds.sh` not run) → the whole suite skips.
- **Snowflake** and **BigQuery** are **opt-in** and never run implicitly — running them against
  live accounts is governed by contract/competition terms, not technical setup. Enable each with
  `LAKEBRIDGE_E2E_SNOWFLAKE=1` / `LAKEBRIDGE_E2E_BIGQUERY=1`, and make sure the key material is in
  the volume (`snowflake.pem` for key-pair auth, `bigquery-sa.json` for BigQuery).

## Security

- **Nothing secret is committed.** The filled `.credentials.yml`, `snowflake.pem`, and
  `bigquery-sa.json` live only in the UC volume and, transiently, at the lakebridge default path.
  The directory `.gitignore` here also blocks them from being added by mistake.
- The volume is the security boundary — protect it with Unity Catalog grants and rotate by
  re-uploading. Nothing here is encrypted-at-rest-in-repo, because nothing is in the repo.
- `credentials.template.yml`, `setup_e2e_creds.sh`, `conftest.py`, `test_profiler_e2e.py`, and this
  README contain no secrets and are safe to commit.
