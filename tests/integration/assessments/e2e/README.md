# Profiler end-to-end (live-database) credentials

These files let the profiler e2e tests run against real source systems, in CI, from **one**
GitHub Actions secret. Nothing secret is committed: the filled credentials live only on your
machine (git-ignored) and inside the secret.

## The 7 sources

| Source           | Section key      | Auth (default)                     | Notes                                  |
|------------------|------------------|------------------------------------|----------------------------------------|
| Snowflake        | `snowflake`      | `pat` (or `key_pair`)              | creds nested under `connection:`       |
| Redshift         | `redshift`       | `sql_authentication` (or `iam`)    | port 5439                              |
| Synapse          | `synapse`        | `SqlPassword` (MSSQL driver)       | dedicated SQL pool                     |
| Legacy Synapse   | `legacy_synapse` | `SqlPassword` (MSSQL driver)       | SQL Server family                      |
| Oracle           | `oracle`         | user/password                      | `service_name`, port 1521              |
| Teradata         | `teradata`       | user/password                      | port 1025                              |
| BigQuery         | `bigquery`       | service-account JSON               | `pairs: [{project, region}]` + JSON key|

All seven live in **one** `.credentials.yml` as sections — that is what the credential manager
reads (`get_credentials("<source>")`). See `credentials.template.yml` for the exact shape.

## Local setup

1. Fill `credentials.template.yml` and save the filled copy to the lakebridge default path
   (git-ignored via the `.credentials.*` rule):

   ```
   ~/.databricks/labs/lakebridge/.credentials.yml
   ```

2. **BigQuery only:** put your service-account JSON key next to it as
   `~/.databricks/labs/lakebridge/bigquery-sa.json`, and set `GOOGLE_APPLICATION_CREDENTIALS`
   to that path when running BigQuery locally.

## Local → one secret

```bash
./bundle_credentials.sh
```

This tars `.credentials.yml` (+ `bigquery-sa.json` if present), base64-encodes it to a single
line, and copies it to your clipboard. Paste that into a repository secret named
**`LAKEBRIDGE_E2E_CREDENTIALS`**. base64 (not a raw multi-line secret) is used so passwords with
`$ " \` or newlines round-trip intact. Re-run the script after rotating any credential.

## One secret → files (in CI)

Add this step before the e2e tests run:

```yaml
- name: Restore profiler e2e credentials
  env:
    CREDS_B64: ${{ secrets.LAKEBRIDGE_E2E_CREDENTIALS }}
  run: |
    set -euo pipefail
    dest="$HOME/.databricks/labs/lakebridge"
    mkdir -p "$dest"
    printf '%s' "$CREDS_B64" | base64 -d | tar xzf - -C "$dest"
    if [ -f "$dest/bigquery-sa.json" ]; then
      echo "GOOGLE_APPLICATION_CREDENTIALS=$dest/bigquery-sa.json" >> "$GITHUB_ENV"
    fi
```

The tests then read the default credentials path — no code changes needed. Gate the job so it
only runs when the secret is present (e.g. a `workflow_dispatch`/scheduled job, or a guard that
skips when `CREDS_B64` is empty), so PRs without the secret don't fail.

## Security

- Never commit the filled `.credentials.yml` or `bigquery-sa.json`. The `.credentials.*` gitignore
  rule covers the former; keep the JSON key out of the repo too.
- The base64 blob is **not** encryption — it is just transport encoding. Treat it as a secret.
- `credentials.template.yml`, `bundle_credentials.sh`, and this README contain no secrets and are
  safe to commit.
