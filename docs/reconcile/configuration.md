# Reconcile Configuration Reference

This page covers the full configuration schema for Lakebridge Reconcile. For setup steps and prerequisites, see the [Reconcile Guide](/lakebridge/docs/reconcile.md).

***

## Report Types[​](#report-types "Direct link to Report Types")

| report type | description                                                                                            | key outputs                                                                                   |
| ----------- | ------------------------------------------------------------------------------------------------------ | --------------------------------------------------------------------------------------------- |
| `schema`    | Reconcile schema of source and target — validate that data types are the same or compatible            | `schema_comparison`, `schema_difference`                                                      |
| `row`       | Reconcile data at row level (hash value comparison). Use when there are no join columns.               | `missing_in_src`, `missing_in_tgt`                                                            |
| `data`      | Reconcile data at row and column level using `join_columns` to identify per-row, per-column mismatches | `mismatch_data`, `missing_in_src`, `missing_in_tgt`, `threshold_mismatch`, `mismatch_columns` |
| `all`       | Combination of `data` + `schema`                                                                       | All outputs above                                                                             |

See [Reconcile Data Flow Examples](/lakebridge/docs/reconcile/dataflow_example.md) for visualizations of each report type.

***

## Config File Naming[​](#config-file-naming "Direct link to Config File Naming")

```text
recon_config_<DATA_SOURCE>_<UNITY_CATALOG_CONNECTION_NAME_OR_CATALOG>_<REPORT_TYPE>.json

```

Place the file in `.lakebridge/` in your Databricks workspace home folder.

> The filename must match the case of `<UNITY_CATALOG_CONNECTION_NAME_OR_CATALOG>` exactly. For example, if the source connection is `conn-oracle-prod`, the filename is `recon_config_oracle_conn-oracle-prod_data.json`.

**Examples by source:**

* Snowflake
* Oracle
* MS SQL Server (incl. Synapse)
* Teradata
* BigQuery
* Databricks

recon\_config\_snowflake\_sample\_data\_all.json

```yaml
source:
    dialect: snowflake
    catalog: sample_data
    schema: default
    uc_connection_name: example_connection_snowflake
target:
    catalog: migrated
    schema: migrated
report_type: all

```

recon\_config\_oracle\_orc\_data.json

```yaml
source:
    dialect: oracle
    uc_connection_name: example_connection_oracle
target:
    catalog: migrated
    schema: migrated
report_type: data

```

recon\_config\_tsql\_silver\_data.json

```yaml
source:
    dialect: mssql
    uc_connection_name: example_connection_mssql
target:
    catalog: migrated
    schema: migrated
report_type: data

```

recon\_config\_teradata\_teradata\_sandbox\_all.json

```yaml
source:
    dialect: teradata
    catalog: DBC
    schema: lakebridge_test
    uc_connection_name: teradata_sandbox
target:
    catalog: migrated
    schema: migrated
report_type: all

```

Required: install a hash UDF on the Teradata source

Teradata does not provide a portable cryptographic hash function in pure SQL, so **you must install your own hash UDF on the Teradata source** before running any `row`, `data`, or `all` report (the `schema` report type does not need it). Any deterministic hash (SHA-256, MD5, etc.) works as long as the source and target sides produce the same string for the same input.

You must also tell Lakebridge how to call the UDF by setting `hash_expression_overrides.source` on the recon config (see [Hash Expression](#hash-expression)).

Refer to the Teradata documentation for how to create and register UDFs on your release and platform. For a real-world example, Teradata publishes an MD5 message-digest UDF you can install as-is or adapt: [MD5 Message Digest UDF](https://downloads.teradata.com/download/extensibility/md5-message-digest-udf).

recon\_config\_bigquery\_sample\_data\_all.json

```yaml
source:
    dialect: bigquery
    catalog: bq_project_name
    schema: sample_dataset
    uc_connection_name: example_connection_bigquery
target:
    catalog: migrated
    schema: migrated
report_type: all

```

Writable dataset required

Results materialize into the `lakebridge_reconcile` dataset. Make sure a dataset with this name exist in the same region as the source data and be writable by the connection's service account.

recon\_config\_databricks\_hms\_schema.json

```yaml
source:
    dialect: databricks
    catalog: hive_metastore
    schema: hr
target:
    catalog: migrated
    schema: migrated
  ...
report_type: data

```

## TABLE Config Schema[​](#table-config-schema "Direct link to TABLE Config Schema")

* Python
* JSON

```python
@dataclass
class Table:
    source_name: str
    target_name: str
    aggregates: list[Aggregate] | None = None
    join_columns: list[str] | None = None
    jdbc_reader_options: JdbcReaderOptions | None = None
    select_columns: list[str] | None = None
    drop_columns: list[str] | None = None
    column_mapping: list[ColumnMapping] | None = None
    transformations: list[Transformation] | None = None
    column_thresholds: list[ColumnThresholds] | None = None
    filters: Filters | None = None
    table_thresholds: list[TableThresholds] | None = None
    sampling_options: SamplingOptions | None = None

```

```json
{
    "source_name": "[SOURCE_NAME]",
    "target_name": "[TARGET_NAME]",
    "aggregates": null,
    "join_columns": ["COLUMN_NAME_1", "COLUMN_NAME_2"],
    "jdbc_reader_options": null,
    "select_columns": null,
    "drop_columns": null,
    "column_mapping": null,
    "transformation": null,
    "column_thresholds": null,
    "filters": null,
    "table_thresholds": null,
    "sampling_options": null
}

```

### Configuration Parameters[​](#configuration-parameters "Direct link to Configuration Parameters")

| Parameter             | Type                    | Required | Description                                                                                                                               |
| --------------------- | ----------------------- | -------- | ----------------------------------------------------------------------------------------------------------------------------------------- |
| `source_name`         | string                  | Yes      | Source table name                                                                                                                         |
| `target_name`         | string                  | Yes      | Target table name                                                                                                                         |
| `join_columns`        | list\[string]           | No       | Primary key columns for row-level joins                                                                                                   |
| `aggregates`          | list\[Aggregate]        | No       | Aggregate reconciliation rules (see [Aggregates](#aggregates-reconciliation))                                                             |
| `jdbc_reader_options` | object                  | No       | JDBC read parallelization (see [JDBC Reader Options](#jdbc-reader-options))                                                               |
| `select_columns`      | list\[string]           | No       | Columns to include in reconciliation                                                                                                      |
| `drop_columns`        | list\[string]           | No       | Columns to exclude from reconciliation                                                                                                    |
| `column_mapping`      | list\[ColumnMapping]    | No       | Source-to-target column name mapping                                                                                                      |
| `transformations`     | list\[Transformation]   | No       | Column-level SQL transformation expressions                                                                                               |
| `column_thresholds`   | list\[ColumnThresholds] | No       | Acceptable variance per column                                                                                                            |
| `table_thresholds`    | list\[TableThresholds]  | No       | Acceptable mismatch rate at table level                                                                                                   |
| `filters`             | Filters                 | No       | WHERE-clause filters for source and/or target                                                                                             |
| `sampling_options`    | SamplingOptions         | No       | Caps the rows pulled into each sample bucket — mismatch, missing-in-source, missing-in-target (see [Sampling Options](#sampling-options)) |

***

## JDBC Reader Options[​](#jdbc-reader-options "Direct link to JDBC Reader Options")

Use JDBC reader options to parallelize reads from large tables.

* Python
* JSON

```python
@dataclass
class JdbcReaderOptions:
    num_partitions: int
    partition_column: str
    lower_bound: str
    upper_bound: str
    fetchsize: int = 0

```

```json
"jdbc_reader_options": {
    "num_partitions": 10,
    "partition_column": "id",
    "lower_bound": "1",
    "upper_bound": "100000",
    "fetchsize": 0
}

```

| Field              | Type   | Required          | Description                                                                      |
| ------------------ | ------ | ----------------- | -------------------------------------------------------------------------------- |
| `num_partitions`   | int    | Yes               | Number of Spark partitions for parallel reads                                    |
| `partition_column` | string | Yes               | Column used for partitioning (choose high-cardinality column for composite keys) |
| `lower_bound`      | string | Yes               | Minimum value for partitioning                                                   |
| `upper_bound`      | string | Yes               | Maximum value for partitioning                                                   |
| `fetchsize`        | int    | No (default: 100) | Rows per JDBC round-trip                                                         |

***

## Column Mapping[​](#column-mapping "Direct link to Column Mapping")

Map source column names to target column names when they differ.

* Python
* JSON

```python
@dataclass
class ColumnMapping:
    source_name: str
    target_name: str

```

```json
"column_mapping": [
  {
    "source_name": "dept_id",
    "target_name": "department_id"
  }
]

```

***

## Drop Columns[​](#drop-columns "Direct link to Drop Columns")

Exclude specific columns from reconciliation (for `data` or `all` report types).

```json
"drop_columns": ["comment", "audit_timestamp"]

```

***

## Transformations[​](#transformations "Direct link to Transformations")

Apply SQL expressions to source or target columns before comparison. Use this when data types or formats differ between systems.

* Python
* JSON

```python
@dataclass
class Transformation:
    column_name: str
    source: str
    target: str | None = None

```

```json
"transformations": [
  {
    "column_name": "unit_price",
    "source": "coalesce(cast(cast(unit_price as decimal(38,10)) as string), '_null_recon_')",
    "target": "coalesce(cast(format_number(cast(unit_price as decimal(38, 10)), 10) as string), '_null_recon_')"
  }
]

```

Handling Nulls

When you provide a transformation expression, Reconcile uses it as-is. **You must handle nulls and cast to string explicitly** in the expression. Use `coalesce(cast(... as string), '_null_recon_')` to avoid null mismatches and incompatibility with the internal transformations that rely on strings.

Timestamp Columns

Convert timestamps to Unix epoch strings for cross-platform comparison:

```python
Transformation(
    column_name='UPDATE_TIMESTAMP',
    source="coalesce(cast(EXTRACT(epoch_millisecond FROM UPDATE_TIMESTAMP) as string), '_null_recon_')",
    target="coalesce(cast(unix_millis(UPDATE_TIMESTAMP) as string), '_null_recon_')"
)

```

***

## Hash Expression[​](#hash-expression "Direct link to Hash Expression")

Override the row-hash function with your own SQL. Each value is raw SQL containing a single `{}` placeholder that Lakebridge replaces with the concatenated hash input.

* `source` is **required** when the block is set: the source-side expression Lakebridge will emit on the source dialect.
* `target` defaults to `sha2({}, 256)` (the target is always Databricks); override it if you use a non-SHA-256 hash.

The block itself is optional for most dialects, omit it to use the source dialect's default. For Teradata sources the block is **mandatory**, since Teradata has no out-of-the-box cryptographic hash.

* Python
* YAML

```python
@dataclass
class HashExpressionOverrides:
    source: str
    target: str = "sha2({}, 256)"

```

```yaml
hash_expression_overrides:
  source: "my_sha256({})"
  target: "sha2({}, 256)"

```

Same hash on both sides

The source and target expressions must produce the **same string for the same logical row** — that is what the comparison query joins on. Lakebridge wraps both sides with `LOWER(...)` automatically, so case alone won't cause mismatches; the bytes produced by the hash itself must match across systems.

***

## Column Thresholds[​](#column-thresholds "Direct link to Column Thresholds")

Allow a bounded variance between source and target column values.

```json
"column_thresholds": [
  {
    "column_name": "price",
    "lower_bound": "-5%",
    "upper_bound": "5%",
    "type": "float"
  }
]

```

| Field         | Type   | Required | Description                                                                           |
| ------------- | ------ | -------- | ------------------------------------------------------------------------------------- |
| `column_name` | string | Yes      | Column to apply the threshold to                                                      |
| `lower_bound` | string | Yes      | Lower bound of acceptable difference                                                  |
| `upper_bound` | string | Yes      | Upper bound of acceptable difference                                                  |
| `type`        | string | Yes      | Column type (supports SQLGlot `DataType.NUMERIC_TYPES` and `DataType.TEMPORAL_TYPES`) |

***

## Table Thresholds[​](#table-thresholds "Direct link to Table Thresholds")

Allow a bounded mismatch rate at the table level.

```json
"table_thresholds": [
  {
    "lower_bound": "0%",
    "upper_bound": "5%",
    "model": "mismatch"
  }
]

```

Bounds must be non-negative, and lower bound must not exceed upper bound. Only `"mismatch"` is supported for `model`.

***

## Filters[​](#filters "Direct link to Filters")

Apply WHERE-clause filters to source and/or target before reconciliation.

```json
"filters": {
  "source": "lower(dept_name) = 'finance'",
  "target": "lower(department_name) = 'finance'"
}

```

note

Filters must use dialect-specific SQL expressions — they are applied directly without any transformation by Reconcile.

***

## Sampling Options[​](#sampling-options "Direct link to Sampling Options")

For `data` and `all` report types, Reconcile captures sample rows for each mismatch / missing-in-source / missing-in-target bucket. `sampling_options` controls **how many** rows are sampled and **how** they are drawn.

note

`sampling_options` applies to **row-level** reconcile (`data` / `all` report types) only. [Aggregates Reconciliation](#aggregates-reconciliation) always samples up to `50` (`DEFAULT_SAMPLE_ROWS`) rows per missing-in-source / missing-in-target set and ignores `sampling_options` (both the sampling method and size).

### SamplingSpecifications[​](#samplingspecifications "Direct link to SamplingSpecifications")

Controls the sample-size policy.

* Python
* JSON

```python
@dataclass
class SamplingSpecifications:
    type: SamplingSpecificationsType = SamplingSpecificationsType.COUNT
    value: int | float = 50

```

```json
"specifications": {
    "type": "count",
    "value": 50
}

```

| field   | type                     | description                                                                                                   | default |
| ------- | ------------------------ | ------------------------------------------------------------------------------------------------------------- | ------- |
| `type`  | `count` \| `fraction`    | `count` = absolute row cap. `fraction` is reserved and currently disabled — passing it raises a config error. | `count` |
| `value` | int (float for fraction) | Maximum rows per sample set (mismatch / missing-in-source / missing-in-target).                               | `50`    |

**Engine-aware bounds on `value`:**

Each source has a `max_sample_size` (a property on its connector):

* **Databricks source:** honored up to `50_000`.
* **Non-Databricks source:** honored up to `400`.
* Values above the source's limit are capped to it with a `WARNING` log (`exceeds the limit of <max> for <connector>; capping to <max>`).
* `value <= 0` falls back to `50`.

### SamplingOptions[​](#samplingoptions "Direct link to SamplingOptions")

Wraps `SamplingSpecifications` and selects the sampling method.

* Python
* JSON

```python
@dataclass
class SamplingOptions:
    method: SamplingOptionMethod = SamplingOptionMethod.RANDOM
    specifications: SamplingSpecifications = field(default_factory=SamplingSpecifications)
    stratified_columns: list[str] | None = None
    stratified_buckets: int | None = None

```

```json
"sampling_options": {
    "method": "random",
    "specifications": { "type": "count", "value": 50 },
    "stratified_columns": null,
    "stratified_buckets": null
}

```

| field                | type                     | description                                                                              | default        |
| -------------------- | ------------------------ | ---------------------------------------------------------------------------------------- | -------------- |
| `method`             | `random` \| `stratified` | Sampling strategy.                                                                       | `random`       |
| `specifications`     | SamplingSpecifications   | Size policy (see [above](#samplingspecifications)).                                      | `count` / `50` |
| `stratified_columns` | list\[string]            | **Required when `method = stratified`.** Columns whose values define the strata.         | `null`         |
| `stratified_buckets` | int                      | **Required when `method = stratified`.** Number of strata buckets. Clamped to `[2, 50]`. | `null`         |

When `method = stratified`, rows are bucketed by `pmod(abs(hash(stratified_columns)), stratified_buckets)` and sampling proceeds within each bucket. Missing `stratified_columns` or `stratified_buckets` raises a `ValueError`.

**Examples**

Random sampling (Databricks source) — limit each sample set (mismatch, missing-in-source, missing-in-target) to 500 rows:

```json
"sampling_options": {
  "method": "random",
  "specifications": { "type": "count", "value": 500 }
}

```

Shortest form (relies on defaults of `random` and `count`):

```json
"sampling_options": { "specifications": { "value": 500 } }

```

Stratified sampling — 10 strata on `ss_store_sk`, up to 500 sample rows total (allocated proportionally across buckets):

```json
"sampling_options": {
  "method": "stratified",
  "specifications": { "type": "count", "value": 500 },
  "stratified_columns": ["ss_store_sk"],
  "stratified_buckets": 10
}

```

***

## Aggregates Reconciliation[​](#aggregates-reconciliation "Direct link to Aggregates Reconciliation")

Reconcile aggregate metrics (MIN, MAX, COUNT, SUM, AVG, etc.) between source and target instead of comparing raw rows.

note

Aggregates reconcile always samples up to `50` (`DEFAULT_SAMPLE_ROWS`) rows per missing-in-source / missing-in-target set; [`sampling_options`](#sampling-options) does not apply here.

### Supported Functions[​](#supported-functions "Direct link to Supported Functions")

MIN, MAX, COUNT, SUM, AVG, MEAN, MODE, STDDEV, VARIANCE, MEDIAN

### Aggregate Config[​](#aggregate-config "Direct link to Aggregate Config")

* Python
* JSON

```python
@dataclass
class Aggregate:
    agg_columns: list[str]
    type: str
    group_by_columns: list[str] | None = None

```

```json
{
    "type": "MIN",
    "agg_columns": ["discount"],
    "group_by_columns": ["p_id"]
}

```

note

`select_columns` and `drop_columns` are ignored for aggregate reconciliation. `column_mapping`, `transformations`, `jdbc_reader_options`, and `filters` are applied.

***

## Complete Examples[​](#complete-examples "Direct link to Complete Examples")

### Standard Reconcile Config[​](#standard-reconcile-config "Direct link to Standard Reconcile Config")

```json
{
  "tables": [
    {
      "source_name": "product_prod",
      "target_name": "product",
      "jdbc_reader_options": {
        "num_partitions": 10,
        "partition_column": "p_id",
        "lower_bound": "0",
        "upper_bound": "10000000"
      },
      "join_columns": ["p_id"],
      "drop_columns": ["comment"],
      "column_mapping": [
        { "source_name": "p_id", "target_name": "product_id" },
        { "source_name": "p_name", "target_name": "product_name" }
      ],
      "transformations": [
        {
          "column_name": "creation_date",
          "source": "creation_date",
          "target": "to_date(creation_date,'yyyy-mm-dd')"
        }
      ],
      "column_thresholds": [
        { "column_name": "price", "lower_bound": "-50", "upper_bound": "50", "type": "float" }
      ],
      "table_thresholds": [
        { "lower_bound": "0%", "upper_bound": "5%", "model": "mismatch" }
      ],
      "filters": {
        "source": "p_id > 0",
        "target": "product_id > 0"
      },
      "sampling_options": {
        "method": "random",
        "specifications": { "type": "count", "value": 100 }
      }
    }
  ]
}

```

### Aggregates Reconcile Config[​](#aggregates-reconcile-config "Direct link to Aggregates Reconcile Config")

```json
{
  "tables": [
    {
      "source_name": "product_prod",
      "target_name": "product",
      "join_columns": ["p_id"],
      "aggregates": [
        { "type": "MIN", "agg_columns": ["discount"], "group_by_columns": ["p_id"] },
        { "type": "AVG", "agg_columns": ["discount"], "group_by_columns": ["p_id"] },
        { "type": "MAX", "agg_columns": ["p_id"], "group_by_columns": ["creation_date"] },
        { "type": "SUM", "agg_columns": ["p_id"] }
      ],
      "jdbc_reader_options": {
        "num_partitions": 10,
        "partition_column": "p_id",
        "lower_bound": "0",
        "upper_bound": "10000000"
      }
    }
  ]
}

```

***

Key Considerations

1. Column names are always converted to lowercase.
2. Case insensitivity and collation are not currently supported.
3. Always reference **source** column names in all configs, except `transformations` and `filters` — those use dialect-specific SQL applied directly.
4. When no user transformation is provided, Reconcile applies default transformations based on the source data type.
